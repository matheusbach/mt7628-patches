# MT7628 TX-power candidates

**This is instrumentation and MCU result validation, not a TX-power fix or
a firmware image. It does
not establish improved range.** It was initially prepared offline and later
loaded on one WL-WN570HN2-B after separate authorization, as noted below.

A functional candidate using command 11's attenuation byte was prepared and
compiled, but review blocked it from hardware use; it is archived outside this
checkout, as described below. The current checkout also contains a separate,
bounded candidate that caps command 11's calibrated targets to the driver's
current limit. It has host payload tests only and is not a validated RF fix.
The selected MCU-result validation was tested using temporary RAM loads and
later persistently installed at the user's explicit request. No range
correction has been validated.

Base revision: `0936617dc529ff9da0385221d3fa6de732231177`.

## Why this exists

An MT7628 board reported different configured TX-power values while repeated
scans from another computer did not show a clear corresponding signal change.
That is consistent with the symptom in
[mt76 #981](https://github.com/openwrt/mt76/issues/981) and
[OpenWrt #5237](https://github.com/openwrt/openwrt/issues/5237), but does not
prove either the cause or that the board is transmitting below its capability.

At the time of investigation, both issues were open and the relevant upstream
`mt7603/mcu.c` matched the version installed on the board. A comment in #981
suggests that the calibrated target upload may override the requested limit.
That suggestion is not a validated correction. The related initialization
change in mt76 PR #1138 explicitly excludes MT7628 and is not applied here.

## Persisted logging-only build

Only `mt7603/mcu.c` changes in the diagnostic driver. On MT7628 it logs:

- Command `08`: channel-switch request, requested total dBm and the existing
  per-chain limit in half-dBm units, followed by the transport return value.
- Command `11`: original raw EEPROM targets, outgoing target bytes and current
  limit, followed by the transport return value.

The request bytes, command order, calibration data, SAR constraints, 3 dB
two-chain correction and return values remain unchanged. No flash or EEPROM
write is added. Other MT7603-family devices do not emit the diagnostic logs.

`ret=0` means the driver's command completed without a reported transport
error. It does not measure actual RF power or prove that the firmware applied
the requested limit. Raw EEPROM targets are not interpreted as dBm in the log.
The log contains no MAC addresses, Wi-Fi keys or full calibration dump.

## Rejected functional candidate, never installed

Read-only comparison with the OEM module identified a control field in the
last byte of command `11`. Although named `reserved` in mt76, the OEM uses it
for a positive power reduction in dB. Its percentage selector emits values
0, 1, 3, 6, 9 and 12. The OEM also sends command `08` followed by `11`, so
command ordering alone is not evidence of the reported fault.

The rejected candidate kept the calibrated targets and every other request
byte unchanged. On MT7628 only, it sent a reduction in that final byte:

```
power_drop_db = ceil((tx_power_limit - txpower_cur) / 2)
```

Both inputs are the driver's existing half-dBm values, after the current
SAR, two-chain correction and calibration-cap calculation. The rounding
selects lower power when the difference is an odd half-dBm. No fixed test
attenuation, new setting, EEPROM write or regulatory-limit increase is added.
Other MT7603-family devices kept a zero last byte. Its log additionally exposed
`power_drop_db` for auditing the request.

This is a candidate implementation, not proof that the firmware consumes the
field as assumed. In particular, values above 12 dB, the absolute calibration
reference and interaction with the command `08` limits still need RF validation.
If both commands independently attenuate, this could reduce power twice.
Correcting attenuation control would also not explain or guarantee recovery
of the range reported for the original firmware. Do not install or publish
this candidate as a demonstrated range fix on the strength of host tests.
Review identified these as blocking issues, so the behavioral changes were
removed from this checkout. The source/test snapshot and compiled artifact
remain under `/tmp/opencode/mt7628-correction/` only for offline analysis.

The implementation was written locally from the protocol observation, not
copied from OEM code. No proprietary module, firmware or source is included
in this patch.

## Bounded command-11 target candidate in this checkout

The `mt7603_mcu_set_channel()` path already computes a per-chain limit in
half-dBm after SAR, the two-chain correction and the EEPROM-derived cap, and
sends it in command `08`. The following command `11` previously sent the raw
EEPROM targets without applying that current limit. The working hypothesis is
that these higher absolute targets can defeat the requested reduction.

For MT7628 only, the candidate decodes each command-11 EEPROM target using the
same sign/validity-bit interpretation used when deriving the driver's power
limit. It lowers a target only when that target exceeds `mphy.txpower_cur`,
preserving bit 7 and signed-magnitude encoding. It never raises a target,
changes EEPROM, or alters command `08`, SAR, command order or other chipsets.
The signed-magnitude target can represent no value below `-63` half-dBm, so
the driver rejects a lower MT7628 limit with `-ERANGE` before sending either
power command rather than wrapping it into a higher value.
The command log now distinguishes original EEPROM bytes from outgoing target
bytes.

Host tests verify request construction, the negative-limit encoding, signed
and flagged EEPROM targets, unchanged lower calibrated targets, other-chip
payload identity, the `-63` representable boundary and rejection below it, and
unchanged EEPROM contents. These tests do not emulate the MCU or measure RF.
The earlier fixed-byte-12 test did not change receiver RSSI and does not
validate this separate hypothesis.

### Temporary router load

On 2026-11-03, at the user's request, the module with SHA-256
`d895139c2fbe01e84b94b7cd09e911c7a78ad98e50236172e03aac569cb86321` was loaded
from `/tmp` after direct wired SSH recovery was confirmed. It remains loaded in
RAM; the staged copy was removed after loading. The persistent
`/lib/modules/6.18.55/mt7603e.ko` remained at SHA-256
`8d13cfd2e0bc6a9f159e3ef117976738632dab5d7ada1078043476a274d244c6`, so reboot
or driver reload returns to that installed module.

All three BSSes came back, the Starlink connection stayed active, `uci changes`
was empty, and hashes of all `/etc/config/*` files matched before and after.
Command `08` and `11` returned matching MCU CIDs with firmware status zero; this
is not an RF measurement. At the active 30 dBm request, the log showed
`cap_half_dbm=54`, EEPROM bytes `0x30/0x30`, and unchanged outgoing targets
`0x30/0x30`. Since those calibrated targets are already below the current cap,
this load did not exercise the new reduction path. No RF-power or range change
has been demonstrated.

## MCU result validation candidate

The original parser checks the sequence but ignores a firmware rejection.
For MT7628 commands `08`, `11` and `21` the current checkout additionally checks
extended command results: event `ed/00`, complete 20-byte reply, matching
command ID and LE32 firmware status. Truncated or wrong-command results return
`-EPROTO`; a nonzero firmware status returns `-EIO`. Existing timeout, sequence
retry and other-chip/command/event behavior is preserved. Reply metadata and
the decoded firmware status are logged without device identifiers or secrets.

The result-validation change changes **no outgoing RF request bytes**.
Temporary loads confirmed real `ed/00`, 20-byte replies, matching CIDs and status zero for
commands `08`, `11` and the EEPROM-to-MCU-RAM upload `21`. Thus firmware rejection
does not explain the observed lack of RSSI response on this board.

The initializer still has a pre-existing unchecked upload return. A proposed
early-return fix was rejected before hardware use because the post-DMA failure
path needs a complete IRQ/DMA/MCU unwind, not just a return statement. Its patch,
test and **never-loaded** binary are archived outside the checkout. Do not
confuse the tested parser with that rejected initialization change.

## Hardware continuation

Fresh passive nl80211 scans on the notebook, without changing its association,
showed similar RSSI for requested totals of 22 and 10 dBm. A separately tested,
RAM-only module sending exactly 12 in the final command-11 byte also did not
produce the expected reduction. It was removed and the persistent diagnostic
restored. This does not establish absolute RF power or unicast behavior.

Both E1/E2 OEM microfirmware arrays were compared in memory and found identical
to the repository and installed binaries. The five TMAC table registers read
at `0x21620..0x21630` remained `0x04040404` at 22/10/22 dBm. Only the debugfs read
selector was temporarily changed and restored; no RF register was written.
These observations narrow the investigation but do not prove an RF correction.

## Offline check

Run on the build computer, not the router:

```sh
python3 tests/mt7603_txpower_diag.py
python3 tests/mt7603_mcu_status.py
git diff --check
```

The test compiles the actual last two request-builder functions from
`mt7603/mcu.c` using a host C compiler and Python's standard library. Only the
hardware transport, SAR lookup and device identity are substituted. It checks
complete outgoing payloads, the MT7628 command-11 target cap, one/two-chain
limits, SAR and EEPROM caps, negative limits, signed/flagged calibration bytes,
HT20/HT40, failure propagation, diagnostic fields and unchanged EEPROM.
The rejected candidate's separate archived tests also check the power-reduction
field, rounding and the unchanged message for other devices. Neither suite is
a firmware emulator or an RF test.
The status suite compiles the actual response parser against independently
constructed wire bytes, checking complete result bounds, endian conversion,
firmware errors, command/event matching and existing timeout/retry behavior.

Cross-build against the **exact** configured OpenWrt kernel, backport headers
and toolchain used for the installed module. Use Kbuild's `M=` and `MO=` to
keep outputs separate. Match OpenWrt's compiler flags, including
`-fno-caller-saves`; merely matching `vermagic` is not sufficient.

Generated artifacts and the local verification record are kept outside this
checkout, under `/tmp/opencode/mt7628-offline/`. The unrelated modified device
definition in the original OpenWrt checkout is not part of this patch.
The rejected functional candidate's separate build is under
`/tmp/opencode/mt7628-correction/`; it must not be installed. It did not
overwrite the logging-only artifact or the installed router module.

## Hardware-test gate

Before any new hardware test:

1. Confirm independent wired management and a person able to power-cycle the
   router. Do not change the notebook's Wi-Fi connection without permission.
2. Back up the installed module and independently verify matching ABI, imported
   symbols and the unmodified baseline build. Check module-loader availability.
3. Get explicit approval for the temporary test and its Wi-Fi interruption.
4. If approved, load only a temporary module from RAM. Do not overwrite files in
   `/lib/modules`, install packages, change boot scripts or add autoload entries.
5. Start with the existing power limit, then only reduce it to positive values
   for the contrast test. Do not increase regulatory limits or alter factory data.
6. Measure the AP at a stationary, independent receiver. Compare repeated
   before/after/back windows. Prefer per-frame dBm measurements and bidirectional
   traffic; a scan percentage alone is not an RF-power measurement.
7. Restore the installed module or reboot. With no persistence changes, reboot
   is intended to load the original installed driver. A kernel hang can still
   require a physical power cycle. This is lower risk, not zero risk.

The bootloader's HTTP uploader must not be described as a RAM-only boot path:
it can write flash. No full image upload is needed for this diagnostic module.
Do not reflash while the original OEM vendor/hw partition boundaries remain
unresolved in the custom board definition.

### Authorized deployment on the investigated board

The user later explicitly requested persistent installation and connected an
Ethernet cable. Wired SSH was verified independently of the notebook's Wi-Fi.
The original module and settings were backed up privately on both devices.
Only the diagnostic module file in the overlay was replaced; bootloader, full
firmware image, factory calibration and UCI settings were not changed.

The loaded module produced the expected command 08/11 logs. The three BSSes
returned with the existing channel 6, HT20 and 22 dBm configuration. All ten
`/etc/config` file hashes, selected account/boot configuration hashes and the
factory-partition hash remained unchanged. A two-minute observation found no
radio reset counters incrementing. There was no full reboot test or RF-power
improvement measurement.

A fixed `phy0-ap0` readiness check initially timed out even though the AP had
returned on `phy1-ap0`; automatic recovery restored the original module. The
check was corrected to discover the current BSS interface before retrying.
Successful activation then used `phy3-ap0`. Transient PHY numbering must not be
treated as a stable device identifier.

**For this persistent deployment, reboot is NOT rollback.** The original still
exists in `/rom`, but restoring it requires working management or recovery
access. No automatic boot-time rollback was installed. Private operational
records are outside this checkout; never publish the configuration archives.

## Later persistent result-validation deployment

After the RAM tests, the user explicitly requested applying the tested version
for a later practical test. The module with SHA-256
`8d13cfd2e0bc6a9f159e3ef117976738632dab5d7ada1078043476a274d244c6`
was atomically installed in the overlay and loaded. Fresh command 21/08/11
responses were decoded successfully. All ten configuration files, selected
account/boot files and factory calibration retained their hashes. The previous
logging-only module was privately backed up on both devices, as were settings.

No outgoing RF power request, rejected attenuation formula or rejected init
guard was applied. The existing three BSSes returned on channel 6, HT20 and
22 dBm. No full reboot test or range gain was demonstrated. This installation
survives reboot; reverting requires restoring the saved previous module.

## Before publishing an RF correction

First prove which command or setting causes the RF discrepancy, then add a
focused failing regression test and the smallest corrective patch. Test the
actual radio, including rollback, before calling it fixed. A host test of
request bytes cannot demonstrate a range improvement.

Publish only the patch, reproducible test and sanitized results, crediting the
earlier reports. Do not upload device backups, credentials, MAC addresses,
factory EEPROM, signing keys or the OEM binary module. Preserve the source
license. Confirm authorship and DCO sign-off with the contributor rather than
inventing identity or certification. No GitHub publication has been performed.
