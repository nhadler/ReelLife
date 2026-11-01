"""Cue map layout (cues.services.cuemap)."""

from cues.services import cuemap

REEL1 = cuemap.Segment(1, "1", 1_000, 2_000)
REEL2 = cuemap.Segment(2, "2", 5_000, 8_000)


def _cue(
    picture: int, tc_in: int, tc_out: int | None, **kw: str
) -> cuemap.CueSpot:
    return cuemap.CueSpot(
        number=kw.get("number", "1m01"),
        title=kw.get("title", "Main"),
        status=kw.get("status", "Composed"),
        color=kw.get("color", "#0284c7"),
        picture_id=picture,
        tc_in=tc_in,
        tc_out=tc_out,
    )


def test_reels_are_laid_end_to_end_by_length() -> None:
    result = cuemap.layout([REEL1, REEL2], [])
    assert [(r.x, r.width, r.label) for r in result.reels] == [
        (0, 250, "Reel 1"),
        (250, 750, "Reel 2"),
    ]
    assert not result.empty


def test_cues_sit_inside_their_reel() -> None:
    result = cuemap.layout([REEL1, REEL2], [_cue(2, 5_400, 5_800)])
    (block,) = result.cues
    assert (block.x, block.width) == (350, 100)
    assert block.label == "1m01 Main (Composed)"
    assert block.color == "#0284c7"


def test_cues_are_clamped_and_never_vanish() -> None:
    result = cuemap.layout(
        [REEL1],
        [
            _cue(1, 500, 1_100),  # starts before the reel
            _cue(1, 1_990, 2_500),  # ends after it
            _cue(1, 1_500, None, title="", status="", color=""),  # no OUT
        ],
    )
    assert [(b.x, b.width) for b in result.cues] == [
        (0, 100),
        (990, 10),
        (500, cuemap.MIN_BLOCK),
    ]
    assert result.cues[2].label == "1m01"
    assert result.cues[2].color == cuemap.UNSET_COLOR


def test_cues_in_unknown_pictures_are_skipped() -> None:
    assert not cuemap.layout([REEL1], [_cue(9, 1_200, 1_300)]).cues


def test_no_usable_reels_means_no_map() -> None:
    result = cuemap.layout([cuemap.Segment(1, "1", 10, 10)], [_cue(1, 10, 20)])
    assert result.empty
    assert not result.cues
