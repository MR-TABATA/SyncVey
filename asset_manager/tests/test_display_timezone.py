"""画面に出す時刻のタイムゾーン。

保存は今までどおり UTC（`USE_TZ = True`）。変えたのは表示だけで、
データベースの中身には触らない。

なぜ直したか: `TIME_ZONE` が `'UTC'` 固定で、しかも画面のどこにも UTC と
書いていなかった。JST の利用者には **9 時間ずれた時刻が、ずれていると分からない
形**で出ていた。ドリフト検知は「いつ変わったか」が本体で、AWS のコンソールや
CloudTrail と突き合わせる場面がある。**ラベルの無い 9 時間は、そのまま読み違いになる。**
"""
import datetime
import importlib

from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

# UTC の同じ瞬間。JST では翌日の 09:10 になる。
MOMENT = datetime.datetime(2026, 9, 4, 0, 10, tzinfo=datetime.timezone.utc)

# テンプレートが実際に使っている書式（`T` がタイムゾーン名）。
FORMAT = '{{ v|date:"Y-m-d H:i T" }}'


def _render(value):
    return Template(FORMAT).render(Context({"v": value}))


class DisplayTimeZoneTests(SimpleTestCase):
    @override_settings(TIME_ZONE="UTC")
    def test_utc_is_shown_as_utc(self):
        self.assertEqual(_render(MOMENT), "2026-09-04 00:10 UTC")

    @override_settings(TIME_ZONE="Asia/Tokyo")
    def test_same_moment_localised_and_labelled(self):
        # 日付が変わる ── ラベルが無いと「前日の夜」に読める。
        self.assertEqual(_render(MOMENT), "2026-09-04 09:10 JST")

    @override_settings(TIME_ZONE="America/New_York")
    def test_any_zone_says_which_one(self):
        self.assertEqual(_render(MOMENT), "2026-09-03 20:10 EDT")


class TimeZoneSettingTests(SimpleTestCase):
    def test_time_zone_follows_the_host(self):
        """`TZ` を渡せば表示がそれに従い、渡さなければ UTC。

        コンテナは何も渡さなければ UTC なので、compose がホストの TZ を渡す。
        既定を UTC にしてあるのは、決め打ちより曖昧でないほうがいいため。
        """
        import config.settings as s

        for env, expected in [({}, "UTC"), ({"TZ": "Asia/Tokyo"}, "Asia/Tokyo")]:
            with self.subTest(env=env or "未設定"):
                with self.settings():  # 副作用を持ち込まない
                    import os

                    old = os.environ.pop("TZ", None)
                    try:
                        os.environ.update(env)
                        importlib.reload(s)
                        self.assertEqual(s.TIME_ZONE, expected)
                    finally:
                        os.environ.pop("TZ", None)
                        if old is not None:
                            os.environ["TZ"] = old
                        importlib.reload(s)


class TemplatesLabelTheirTimestampsTests(SimpleTestCase):
    def test_absolute_times_carry_a_zone(self):
        """時刻まで出しているテンプレートは、必ずタイムゾーンも出す。

        新しく `H:i` を足したときに、ラベルを忘れて元の状態へ戻らないよう機械で見る。
        日付だけ（`Y-m-d`）と相対表示（`timesince`）は対象外 ── ずれても意味が変わらない。
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[2] / "templates"
        offenders = []
        for path in root.rglob("*.html"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r"date:['\"]([^'\"]*H:i[^'\"]*)['\"]", line):
                    if "T" not in m.group(1) and "e" not in m.group(1):
                        offenders.append(f"{path.name}:{n} {m.group(1)}")
        self.assertEqual(offenders, [], "タイムゾーンの無い時刻表示: " + ", ".join(offenders))
