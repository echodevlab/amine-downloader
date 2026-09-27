from amine_downloader.models import ParsedTitle
from amine_downloader.renamer import DEFAULT_TEMPLATE, render


def make(**kwargs) -> ParsedTitle:
    defaults = dict(raw="raw", group="Group", title="Title", season=1, episode="5", resolution="1080p")
    defaults.update(kwargs)
    return ParsedTitle(**defaults)


def test_default_template():
    assert render(make()) == "[Group] Title S01E05 [1080p]"


def test_empty_parts_are_cleaned():
    assert render(make(group="", resolution="")) == "Title S01E05"


def test_episode_padding_width():
    assert render(make(episode="7"), template="E{episode:3}") == "E007"
    assert render(make(season=2, episode="12")) == "[Group] Title S02E12 [1080p]"


def test_fractional_episode():
    assert render(make(episode="5.5")) == "[Group] Title S01E05.5 [1080p]"


def test_invalid_characters_are_stripped():
    name = render(make(title='Bad:Name?'), template="{title}")
    assert ":" not in name and "?" not in name


def test_custom_template_tokens():
    assert render(make(), template="{group} - {title} - {episode}") == "Group - Title - 05"


def test_default_template_constant():
    assert "{title}" in DEFAULT_TEMPLATE
