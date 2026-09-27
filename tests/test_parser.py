from amine_downloader.parser import parse_title


def test_chinese_bracket_title_with_year():
    parsed = parse_title(
        "[北宇治字幕组] 关于我转生变成史莱姆这档事(2017)[11][1080P][AVC-YUV420P8]"
    )
    assert parsed.group == "北宇治字幕组"
    assert parsed.title == "关于我转生变成史莱姆这档事"
    assert parsed.episode == "11"
    assert parsed.resolution == "1080p"


def test_western_season_episode():
    parsed = parse_title("Oshi no Ko S02E05 [1080p]")
    assert parsed.title == "Oshi no Ko"
    assert parsed.season == 2
    assert parsed.episode == "5"


def test_chinese_season_and_bracket_episode():
    parsed = parse_title("[桜都字幕组] 转生史莱姆这档事 第三季 [01][1080p][简繁内封]")
    assert parsed.group == "桜都字幕组"
    assert parsed.title == "转生史莱姆这档事"
    assert parsed.season == 3
    assert parsed.episode == "1"
    assert parsed.language == "CHS&CHT"


def test_junk_prefix_is_ignored_for_title():
    parsed = parse_title("【喵萌奶茶屋】★01月新番★[我推的孩子][01][1080p][简日双语][招募翻译]")
    assert parsed.group == "喵萌奶茶屋"
    assert parsed.title == "我推的孩子"
    assert parsed.episode == "1"
    assert parsed.language == "CHS"


def test_version_suffix_is_ignored():
    parsed = parse_title("Some Anime - 13v2 [720p]")
    assert parsed.title == "Some Anime"
    assert parsed.episode == "13"


def test_chinese_episode_marker():
    parsed = parse_title("Spy x Family 第25话")
    assert parsed.title == "Spy x Family"
    assert parsed.episode == "25"


def test_source_and_language():
    parsed = parse_title(
        "[Lilith-Raws] 葬送的芙莉莲 - 05 [1080p][Baha][WEB-DL][AAC AVC][CHT]"
    )
    assert parsed.source == "WEB-DL"
    assert parsed.language == "CHT"
    assert parsed.extra
