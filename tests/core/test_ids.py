import uuid

from cs.core.ids import new_id


def test_new_id_is_uuid7_and_monotonic():
    a = new_id()
    b = new_id()
    assert isinstance(a, uuid.UUID)
    assert a.version == 7
    assert str(a) < str(b)  # time-ordered within the same millisecond via counter
