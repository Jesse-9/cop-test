"""阶段四物料谱系/区域容量/配对契约测试."""

from __future__ import annotations

import pytest

from stage4 import config as cfg
from stage4.materials import (ItemType, MaterialFlowError, MaterialRegistry,
                              RecordState)


def _registry_with_pair():
    reg = MaterialRegistry()
    reg.register(1, ItemType.SEMI_CE, "Part_0001", zone="buffer_e_c",
                 sim_time=1.0)
    reg.register(2, ItemType.PART_DE, "Part_0002", zone="buffer_e_d",
                 sim_time=1.5)
    return reg


def test_register_and_zone_move():
    reg = MaterialRegistry()
    rec = reg.register(1, ItemType.RAW_A, "Part_0001", zone="line_s1a")
    assert rec.state is RecordState.CREATED
    reg.occupy(1, "handoff_ac", 1.0, state=RecordState.READY,
               reason="A place")
    assert reg.by_uid(1).zone == "handoff_ac"
    assert reg.by_uid(1).state is RecordState.READY
    assert reg.zone_free("line_s1a")


def test_zone_capacity_rejects_second_occupant():
    reg = MaterialRegistry()
    reg.register(1, ItemType.RAW_A, "Part_0001", zone="line_s1a")
    reg.register(2, ItemType.RAW_A, "Part_0002", zone="arm_A")
    assert not reg.zone_free("line_s1a")
    with pytest.raises(MaterialFlowError):
        reg.occupy(2, "line_s1a", 1.0)


def test_process_transition_only_defined_pairs():
    reg = MaterialRegistry()
    reg.register(1, ItemType.RAW_A, "Part_0001", zone="line_s1a")
    reg.process(1, "A", 1.0)
    assert reg.by_uid(1).item_type is ItemType.SEMI_AC
    with pytest.raises(MaterialFlowError):
        reg.process(1, "B", 2.0)


def test_join_success_records_parents_and_consumes():
    reg = _registry_with_pair()
    result = reg.commit_join(final_uid=9, sim_time=3.0,
                             final_alias="Part_0009")
    assert result.ok
    assert result.semi_uid == 1 and result.part_uid == 2
    semi = reg.by_uid(1)
    part = reg.by_uid(2)
    assert semi.state is RecordState.CONSUMED
    assert part.state is RecordState.CONSUMED
    final = reg.by_uid(9)
    assert final.item_type is ItemType.FINAL
    assert final.parents == (1, 2)
    assert reg.final_records()[0].uid == 9
    assert reg.join_history[0]["pair_key"] == "1+2"
    genealogy = reg.genealogy(9)
    assert [row["uid"] for row in genealogy["parents"]] == [1, 2]


def test_join_missing_input_returns_wait_without_final():
    reg = MaterialRegistry()
    reg.register(1, ItemType.SEMI_CE, "Part_0001", zone="buffer_e_c")
    result = reg.commit_join(final_uid=9, sim_time=1.0,
                             final_alias="Part_0009")
    assert not result.ok
    assert "MISSING_INPUT" in result.reason
    assert not reg.final_records()
    assert reg.by_uid(1).state is not RecordState.CONSUMED


def test_join_wrong_zone_is_not_a_pair():
    reg = MaterialRegistry()
    reg.register(1, ItemType.SEMI_CE, "Part_0001", zone="line_ce")
    reg.register(2, ItemType.PART_DE, "Part_0002", zone="buffer_e_d")
    result = reg.commit_join(final_uid=9, sim_time=1.0,
                             final_alias="Part_0009")
    assert not result.ok
    assert "SEMI_CE" in result.reason


def test_join_replay_is_rejected():
    reg = _registry_with_pair()
    first = reg.commit_join(final_uid=9, sim_time=3.0,
                            final_alias="Part_0009")
    assert first.ok
    replay = reg.commit_join(final_uid=10, sim_time=4.0,
                             final_alias="Part_0010")
    assert not replay.ok
    assert len(reg.final_records()) == 1
    assert reg.consumed_pairs == {"1+2"}


def test_join_duplicate_final_uid_rejected():
    reg = _registry_with_pair()
    reg.register(9, ItemType.FINAL, "Part_0009", zone="output_e")
    result = reg.commit_join(final_uid=9, sim_time=3.0,
                             final_alias="Part_0009b")
    assert not result.ok
    assert "FINAL_UID_EXISTS" in result.reason


def test_wip_excludes_consumed_and_final():
    reg = _registry_with_pair()
    reg.commit_join(final_uid=9, sim_time=3.0, final_alias="Part_0009")
    reg.register(3, ItemType.SEMI_CE, "Part_0003", zone="buffer_e_c")
    wip = reg.wip()
    assert wip["total"] == 1
    assert wip["by_type"] == {"SEMI_CE": 1}


def test_consumed_material_cannot_move():
    reg = _registry_with_pair()
    reg.commit_join(final_uid=9, sim_time=3.0, final_alias="Part_0009")
    with pytest.raises(MaterialFlowError):
        reg.occupy(1, "output_e", 4.0)


def test_zone_capacity_defaults_one_for_arm_and_source():
    reg = MaterialRegistry()
    assert reg.capacity_of("arm_A") == 1
    assert reg.capacity_of("source_x") == 1
    assert reg.capacity_of("output_e") == cfg.ZONE_CAPACITY["output_e"]
