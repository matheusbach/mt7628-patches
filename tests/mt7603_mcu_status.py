#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause-Clear
"""Exercise the real MCU response parser with complete wire-format replies."""
import errno
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
typedef uint8_t u8;
typedef uint16_t __le16;
typedef uint32_t u32;
#define __packed __attribute__((packed))
#define __aligned(n) __attribute__((aligned(n)))
#include "mt7603/mcu.h"
struct mt76_dev { void *dev; };
struct mt7603_dev { struct mt76_dev mt76; int mcu_hang; int soc; };
struct sk_buff { u8 *data; unsigned len; };
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define is_mt7628(d) ((d)->soc)
#define MT7603_WATCHDOG_TIMEOUT 10
static inline u32 get_unaligned_le32(const void *ptr)
{
    const u8 *b = ptr;
    return b[0] | ((u32)b[1] << 8) | ((u32)b[2] << 16) | ((u32)b[3] << 24);
}
static void __attribute__((format(printf, 2, 3)))
dev_err(void *dev, const char *fmt, ...)
{
    (void)dev;
    va_list ap;
    va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
}
#define dev_info dev_err
"""
MAIN = r"""
int main(int argc, char **argv)
{
    if (argc != 9) return 2;
    int cmd = atoi(argv[1]), seq = 7;
    unsigned long status = strtoul(argv[2], NULL, 0);
    int len = atoi(argv[3]), cid = atoi(argv[4]), actual_seq = atoi(argv[5]);
    struct mt7603_dev dev = { .soc = atoi(argv[6]) };
    u8 data[32] = {0};
    data[0] = 20;
    data[3] = 0xe0;
    data[4] = atoi(argv[8]);
    data[5] = actual_seq;
    data[8] = atoi(argv[7]);
    data[12] = cid;
    for (int i = 0; i < 4; i++) data[16 + i] = status >> (i * 8);
    struct sk_buff skb = { .data = data, .len = len < 0 ? 0 : (unsigned)len };
    int ret = mt7603_mcu_parse_response(&dev.mt76, cmd, len < 0 ? NULL : &skb, seq);
    printf("RETURN %d\nHANG %d\n", ret, dev.mcu_hang);
    return 0;
}
"""


class McuStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="mt7603-status-")
        cls.addClassCleanup(cls.tmp.cleanup)
        source = (ROOT / "mt7603/mcu.c").read_text()
        start = source.index("static int\nmt7603_mcu_parse_response(")
        end = source.index("static int\nmt7603_mcu_skb_send_msg(", start)
        cfile = Path(cls.tmp.name) / "parser.c"
        cfile.write_text(HARNESS + source[start:end] + MAIN)
        cls.binary = Path(cls.tmp.name) / "parser"
        subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-Wall",
                        "-Wextra", "-Werror", "-I", str(ROOT), str(cfile),
                        "-o", str(cls.binary)], check=True)

    def parse(self, cmd=17, status=0, size=20, cid=None, seq=7, soc=1, event=0, eid=237):
        cid = cmd if cid is None else cid
        output = subprocess.check_output([str(self.binary), *map(str,
            (cmd, status, size, cid, seq, soc, event, eid))], text=True)
        result = {line.split()[0]: int(line.split()[1]) for line in output.splitlines()
                  if line.startswith(("RETURN ", "HANG "))}
        return result, output

    def test_firmware_rejection_is_not_reported_as_transport_success(self):
        for cmd in (8, 17, 33):
            for status in (1, 0x12345678):
                with self.subTest(cmd=cmd, status=status):
                    result, log = self.parse(cmd=cmd, status=status)
                    self.assertEqual(result["RETURN"], -errno.EIO)
                    self.assertIn(f"fw_status={status}", log)

    def test_success_is_reported_with_firmware_status(self):
        result, log = self.parse()
        self.assertEqual(result["RETURN"], 0)
        self.assertIn("fw_status=0", log)

    def test_truncated_or_wrong_command_result_is_rejected(self):
        for options in ({"size": 12}, {"size": 19}, {"cid": 8}):
            with self.subTest(options=options):
                self.assertEqual(self.parse(**options)[0]["RETURN"], -errno.EPROTO)

    def test_existing_timeout_sequence_and_header_validation_are_preserved(self):
        for options, want in (({"size": -1}, -errno.ETIMEDOUT),
                              ({"size": 11}, -errno.EINVAL),
                              ({"seq": 6}, -errno.EAGAIN)):
            with self.subTest(options=options):
                result, _ = self.parse(**options)
                self.assertEqual(result["RETURN"], want)
                self.assertEqual(result["HANG"], 10 if options.get("size") == -1 else 0)

    def test_other_devices_commands_and_events_keep_existing_behavior(self):
        for options in ({"soc": 0}, {"cmd": -2}, {"cmd": 39}, {"event": 2}, {"eid": 1}):
            with self.subTest(options=options):
                result, log = self.parse(status=1, **options)
                self.assertEqual(result["RETURN"], 0)
                self.assertNotIn("fw_status=", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
