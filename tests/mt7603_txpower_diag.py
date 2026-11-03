#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""Run the actual MCU request builders with a host-side transport substitute.

This checks request bytes, error propagation and diagnostic output. It does
not emulate the MCU, measure RF power, or prove a fix for mt76 issue #981.
Only the last two functions from mt7603/mcu.c are compiled; their hardware
transport, SAR lookup and device identity are supplied by this harness.
"""

import os
import errno
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef int8_t s8;
#define __MT7603_H
#include "mt7603/eeprom.h"
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define BIT(n) (1U << (n))
#define GENMASK(h, l) (((~0U) >> (31 - (h))) & (~0U << (l)))
#define hweight8(a) __builtin_popcount(a)
#define NL80211_CHAN_WIDTH_40 1
#define MT_BW_20 0
#define MT_BW_40 1
#define MCU_EXT_CMD_CHANNEL_SWITCH 0x08
#define MCU_EXT_CMD_SET_TX_POWER_CTRL 0x11
struct ieee80211_channel { unsigned hw_value; int center_freq; };
struct cfg80211_chan_def {
    struct ieee80211_channel *chan;
    int width, center_freq1;
};
struct ieee80211_hw { struct { int power_level; } conf; };
struct mt76_dev { struct { void *data; } eeprom; void *dev; };
struct mt76_phy {
    struct cfg80211_chan_def chandef;
    unsigned antenna_mask;
    int txpower_cur;
};
struct mt7603_dev {
    struct mt76_dev mt76;
    struct mt76_phy mphy;
    struct ieee80211_hw hw;
    int tx_power_limit;
    bool soc;
};
#define mt76_hw(d) (&(d)->hw)
#define is_mt7628(d) ((d)->soc)
static int sar_limit, fail_cmd;
static int mt76_get_sar_power(struct mt76_phy *phy,
                             struct ieee80211_channel *chan, int power)
{
    (void)phy; (void)chan;
    return min(power, sar_limit);
}
static int mt76_mcu_send_msg(struct mt76_dev *dev, int cmd, const void *data,
                            int len, bool wait)
{
    (void)dev;
    if (!wait) abort();
    printf("CMD %d ", cmd);
    for (int i = 0; i < len; i++) printf("%02x", ((const u8 *)data)[i]);
    puts("");
    return cmd == fail_cmd ? -110 : 0;
}
static void __attribute__((unused, format(printf, 2, 3)))
dev_info(void *dev, const char *fmt, ...)
{
    va_list args;
    (void)dev;
    fputs("LOG ", stdout);
    va_start(args, fmt); vprintf(fmt, args); va_end(args);
}
"""
MAIN = r"""
int main(int argc, char **argv)
{
    if (argc != 10) return 2;
    u8 eep[1024] = {
        [0x36] = 0x0e, [0x50] = 0x82, [0x58] = 0x30, [0x5e] = 0x2c,
        [0x59] = 0x9e, [0x5a] = 0x9d, [0x5b] = 0x9d,
        [0x5f] = 0x9d, [0x60] = 0xa0, [0x61] = 0xa3,
    }, before[1024];
    const u8 rates[] = {0xc6, 0xc6, 0xc4, 0xc4, 0xc4, 0xc0, 0xc0,
                        0xc4, 0xc4, 0xc4, 0xc4, 0xc4, 0xc0, 0xc0};
    struct ieee80211_channel chan = {.hw_value = 6, .center_freq = 2437};
    struct mt7603_dev dev = {
        .mt76.eeprom.data = eep,
        .mphy.chandef = {.chan = &chan, .center_freq1 = 2437},
    };
    memcpy(eep + 0xa0, rates, sizeof(rates));
    eep[0x58] = strtoul(argv[8], NULL, 0);
    eep[0x5e] = strtoul(argv[9], NULL, 0);
    memcpy(before, eep, sizeof(before));
    dev.hw.conf.power_level = atoi(argv[1]);
    dev.mphy.antenna_mask = atoi(argv[2]);
    dev.tx_power_limit = atoi(argv[3]);
    sar_limit = atoi(argv[4]);
    dev.soc = atoi(argv[5]);
    fail_cmd = atoi(argv[6]);
    if (atoi(argv[7])) {
        dev.mphy.chandef.width = NL80211_CHAN_WIDTH_40;
        dev.mphy.chandef.center_freq1 += atoi(argv[7]) * 10;
    }
    int ret = mt7603_mcu_set_channel(&dev);
    printf("RETURN %d\nCURRENT %d\nEEPROM_UNCHANGED %d\n", ret,
           dev.mphy.txpower_cur, memcmp(before, eep, sizeof(eep)) == 0);
    return 0;
}
"""


class TxPowerDiagnostic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="mt7603-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        source = (ROOT / "mt7603/mcu.c").read_text()
        functions = source[source.index("static int mt7603_mcu_set_tx_power("):]
        cfile = Path(cls.tmp.name) / "request_builders.c"
        cfile.write_text(HARNESS + functions + MAIN)
        cls.binary = Path(cls.tmp.name) / "request_builders"
        subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-Wall",
                        "-Wextra", "-Werror", "-Wno-sign-compare", "-I", str(ROOT),
                        str(cfile), "-o", str(cls.binary)], check=True)

    def run_case(self, power=22, mask=3, cap=54, sar=127, soc=1, fail=0,
                 bw=0, target0=0x30, target1=0x2c):
        args = (power, mask, cap, sar, soc, fail, bw, target0, target1)
        output = subprocess.check_output([str(self.binary), *map(str, args)], text=True)
        commands, logs, values = [], [], {}
        for line in output.splitlines():
            if line.startswith("CMD "):
                _, cmd, data = line.split()
                commands.append((int(cmd), bytes.fromhex(data)))
            elif line.startswith("LOG "):
                logs.append(dict(token.split("=", 1) for token in line.split() if "=" in token))
            else:
                name, value = line.split()
                values[name] = int(value)
        self.assertEqual(values["EEPROM_UNCHANGED"], 1)
        return commands, logs, values

    def test_outgoing_payloads_keep_calibration_sar_chain_correction_and_limits(self):
        for power, mask, cap, sar, bw, want, target0, target1 in (
                (22, 3, 54, 127, 0, 38, 38, 38),
                (6, 3, 54, 127, 0, 6, 6, 6),
                (0, 3, 54, 127, 0, -6, 0x46, 0x46),
                (22, 1, 54, 127, 0, 44, 44, 44),
                (22, 3, 20, 127, 0, 20, 20, 20),
                (22, 3, 54, 24, 0, 18, 18, 18),
                (22, 3, 54, 127, 1, 38, 38, 38),
                (22, 3, 54, 127, -1, 38, 38, 38)):
            with self.subTest(power=power, mask=mask, cap=cap, sar=sar, bw=bw):
                commands, _, values = self.run_case(power, mask, cap, sar, bw=bw)
                self.assertEqual([cmd for cmd, _ in commands], [8, 17])
                channel, calibration = [data for _, data in commands]
                streams = {1: 1, 3: 2}[mask]
                self.assertEqual(channel, bytes([
                    6, {0: 6, 1: 8, -1: 4}[bw], int(bw != 0), streams, streams,
                ]) + bytes(7) + bytes([want & 255]) * 21 + bytes(3))
                self.assertEqual(values["CURRENT"], want)
                self.assertEqual(calibration, bytes.fromhex(
                    f"06 00 0e {target0:02x} {target1:02x} "
                    "c6 c6 c4 c4 c4 c0 c0 c4 c4 c4 c4 c4 c0 c0 82 "
                    "9e 9d 9d 9d a0 a3 " + "00 " * 18))
                self.assertEqual(values["RETURN"], 0)

    def test_mt7628_caps_both_command_11_targets_to_current_per_chain_limit(self):
        commands, _, values = self.run_case(power=22, mask=3, cap=54, sar=127,
                                             soc=1)
        calibration = commands[1][1]
        self.assertEqual(calibration[3:5], bytes([38, 38]))
        self.assertEqual(values["EEPROM_UNCHANGED"], 1)

    def test_command_11_does_not_raise_calibrated_targets(self):
        commands, _, _ = self.run_case(power=30)
        self.assertEqual(commands[1][1][3:5], bytes([0x30, 0x2c]))

    def test_command_11_preserves_signed_and_validity_bits(self):
        commands, _, _ = self.run_case(target0=0xb0, target1=0xac)
        self.assertEqual(commands[1][1][3:5], bytes([0xa6, 0xa6]))

        commands, _, _ = self.run_case(target0=0x50, target1=0x4c)
        self.assertEqual(commands[1][1][3:5], bytes([0x50, 0x4c]))

    def test_signed_magnitude_limits_are_bounded_before_mcu_sends(self):
        commands, _, values = self.run_case(sar=-57)
        self.assertEqual(values["CURRENT"], -63)
        self.assertEqual(commands[1][1][3:5], bytes([0x7f, 0x7f]))

        commands, _, _ = self.run_case(sar=-57, target0=0xb0, target1=0xac)
        self.assertEqual(commands[1][1][3:5], bytes([0xff, 0xff]))

        commands, _, _ = self.run_case(sar=-14, target0=0x50, target1=0x4c)
        self.assertEqual(commands[1][1][3:5], bytes([0x54, 0x54]))

        for sar in (-58, -122, -123):
            with self.subTest(sar=sar):
                commands, _, values = self.run_case(sar=sar)
                self.assertEqual(commands, [])
                self.assertEqual(values["RETURN"], -errno.ERANGE)

    def test_mt7628_logs_both_requests_and_results_with_explicit_units(self):
        for requested, sar, want, targets in (
                (22, 127, "38", "26/26"), (6, 127, "6", "06/06"),
                (0, 127, "-6", "46/46"), (22, 24, "18", "12/12")):
            with self.subTest(requested=requested, sar=sar):
                _, logs, _ = self.run_case(power=requested, sar=sar)
                self.assertEqual(len(logs), 4, "both MCU requests and responses must be observable")
                self.assertEqual([log["cmd"] for log in logs], ["08", "08", "11", "11"])
                self.assertEqual([log["phase"] for log in logs], ["send", "done", "send", "done"])
                self.assertEqual(logs[0]["requested_dbm"], str(requested))
                self.assertEqual(logs[0]["cap_half_dbm"], want)
                self.assertEqual(logs[2]["eep_target_raw"], "30/2c")
                self.assertEqual(logs[2]["target_raw"], targets)
                self.assertEqual(logs[2]["cap_half_dbm"], want)
                self.assertEqual([logs[i]["ret"] for i in (1, 3)], ["0", "0"])

    def test_channel_failure_is_logged_and_does_not_send_calibration(self):
        commands, logs, values = self.run_case(fail=8)
        self.assertEqual([cmd for cmd, _ in commands], [8])
        self.assertEqual(values["RETURN"], -110)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[-1]["ret"], "-110")

    def test_calibration_failure_is_logged_and_propagated(self):
        commands, logs, values = self.run_case(fail=17)
        self.assertEqual([cmd for cmd, _ in commands], [8, 17])
        self.assertEqual(values["RETURN"], -110)
        self.assertEqual(len(logs), 4)
        self.assertEqual(logs[-1]["ret"], "-110")

    def test_non_mt7628_devices_remain_silent(self):
        commands, logs, values = self.run_case(soc=0)
        self.assertEqual(logs, [])
        self.assertEqual(values["RETURN"], 0)
        self.assertEqual(commands[1][1], bytes.fromhex(
            "06 00 0e 30 2c c6 c6 c4 c4 c4 c0 c0 c4 c4 c4 c4 c4 c0 c0 "
            "82 9e 9d 9d 9d a0 a3 " + "00 " * 18))


if __name__ == "__main__":
    unittest.main(verbosity=2)
