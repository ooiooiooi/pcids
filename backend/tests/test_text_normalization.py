import unittest

from backend.utils.text_normalization import normalize_text, normalize_text_payload


class TextNormalizationTests(unittest.TestCase):
    def test_repairs_known_business_mojibake_terms(self):
        self.assertEqual(normalize_text("澶嶄綅杩愯"), "复位运行")
        self.assertEqual(normalize_text("涓嶅鐞?"), "不处理")
        self.assertEqual(normalize_text("浠呭浣?"), "仅复位")
        self.assertEqual(normalize_text("鍏ㄧ墖鎿﹂櫎"), "全片擦除")
        self.assertEqual(normalize_text("STM32F407VGT6寮€鍙戞澘"), "STM32F407VGT6开发板")
        self.assertEqual(normalize_text("SWD涓嬭浇鍣?"), "SWD下载器")

    def test_repairs_nested_payload_values(self):
        payload = {
            "completion_action": "澶嶄綅杩愯",
            "erase_mode": "鍏ㄧ墖鎿﹂櫎",
            "options": ["涓嶅鐞?", "浠呭浣?"],
        }

        self.assertEqual(
            normalize_text_payload(payload),
            {
                "completion_action": "复位运行",
                "erase_mode": "全片擦除",
                "options": ["不处理", "仅复位"],
            },
        )

    def test_keeps_clean_text_unchanged(self):
        self.assertEqual(normalize_text("复位运行"), "复位运行")
        self.assertEqual(normalize_text("STM32F103C8T6"), "STM32F103C8T6")
        self.assertEqual(normalize_text("Mädchen"), "Mädchen")

    def test_repairs_utf8_text_decoded_as_latin1(self):
        broken = "中文目录/烧录文件.bin".encode("utf-8").decode("latin1")
        self.assertEqual(normalize_text(broken), "中文目录/烧录文件.bin")


if __name__ == "__main__":
    unittest.main()
