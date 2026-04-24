"""KnowledgeItem id 기반 관리 전환 + INSERT 차단 가드 유닛 테스트.

설계 문서: docs/todo/20260420-knowledge-item-id-based-management.md §8.1

검증 대상:
  - context_interpreter._build_knowledge_update (id 가드 + 정규화)
  - context_interpreter._dedup_knowledge_items (id 기반)
  - context_interpreter._merge_updates_into_items (key/is_critical 보존, 승격)
"""

from __future__ import annotations

from src.agents.nodes.reason.context_interpreter import (
    _build_knowledge_update,
    _dedup_knowledge_items,
    _merge_updates_into_items,
    _should_promote,
)
from src.agents.state.state import (
    ConfidenceStatus,
    DeadEnd,
    FailureType,
    KnowledgeItem,
)


EXISTING_ID_SET = {"K1", "K2", "K3"}


# ──────────────────────────────────────────────────────────────────
# INSERT 차단 가드
# ──────────────────────────────────────────────────────────────────

class TestInsertBlocking:
    def test_unknown_id_dropped(self) -> None:
        """unresolved_items에 없는 id → 폐기."""
        ku = {
            "id": "K99",
            "value": "COUNT(*)",
            "new_status": "CONFIRMED",
        }
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "test")
        assert result is None

    def test_missing_id_dropped(self) -> None:
        """id 누락 → 폐기."""
        ku = {
            "key": "measure:지점 수",
            "value": "COUNT(*)",
        }
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "test")
        assert result is None

    def test_empty_id_dropped(self) -> None:
        """id가 빈 문자열 → 폐기."""
        ku = {"id": "", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "test")
        assert result is None

    def test_semantic_key_as_id_dropped(self) -> None:
        """semantic key를 id로 오용 → malformed로 폐기."""
        ku = {
            "id": "measure:지점 수",
            "value": "COUNT(*)",
        }
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "test")
        assert result is None

    def test_non_k_prefix_dropped(self) -> None:
        """K 접두사 없는 id (e.g. 'X1') → malformed 폐기."""
        ku = {"id": "X1", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "test")
        assert result is None

    def test_non_numeric_suffix_dropped(self) -> None:
        """K 뒤가 숫자가 아님 (e.g. 'KA') → malformed 폐기."""
        ku = {"id": "KA", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "test")
        assert result is None


# ──────────────────────────────────────────────────────────────────
# UPDATE 수용 + 정규화
# ──────────────────────────────────────────────────────────────────

class TestUpdateAcceptance:
    def test_valid_id_accepted(self) -> None:
        """유효한 id → UPDATE 수용, value/status/confidence 반영."""
        ku = {
            "id": "K1",
            "value": "COUNT(DISTINCT BLNG_BRCD)",
            "confidence": 0.9,
            "new_status": "CONFIRMED",
            "source": "search_table_meta",
            "evidence": "BLNG_BRCD 컬럼 확인",
        }
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "default")
        assert result is not None
        assert result.id == "K1"
        assert result.value == "COUNT(DISTINCT BLNG_BRCD)"
        assert result.confidence == 0.9
        assert result.status == ConfidenceStatus.CONFIRMED
        assert result.source == "search_table_meta"
        assert result.evidence == ["BLNG_BRCD 컬럼 확인"]

    def test_returned_item_has_blank_key_and_false_critical(self) -> None:
        """UPDATE 아이템은 key="", is_critical=False — 병합 단계에서 기존 KI 보존.

        _build_knowledge_update 는 LLM 이 key/is_critical 를 임의로 덮어쓰지
        못하도록 이들을 응답에서 무시하고 빈 값으로 반환한다. 실제 key/
        is_critical 보존은 _merge_updates_into_items 에서 이루어진다.
        """
        ku = {"id": "K2", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "default")
        assert result is not None
        assert result.key == ""
        assert result.is_critical is False

    def test_id_case_normalized(self) -> None:
        """'k1' → 'K1' 로 정규화."""
        ku = {"id": "k1", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "default")
        assert result is not None
        assert result.id == "K1"

    def test_id_parentheses_stripped(self) -> None:
        """'(K1)' → 'K1' 로 정규화."""
        ku = {"id": "(K1)", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "default")
        assert result is not None
        assert result.id == "K1"

    def test_id_whitespace_stripped(self) -> None:
        """공백 포함 id → strip."""
        ku = {"id": "  K1  ", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "default")
        assert result is not None
        assert result.id == "K1"

    def test_default_source_applied(self) -> None:
        """source 누락 시 default_source 사용."""
        ku = {"id": "K1", "value": "x"}
        result = _build_knowledge_update(ku, EXISTING_ID_SET, "배치해석")
        assert result is not None
        assert result.source == "배치해석"


# ──────────────────────────────────────────────────────────────────
# dedup — id 기반
# ──────────────────────────────────────────────────────────────────

class TestDedup:
    def test_same_id_keeps_highest_confidence(self) -> None:
        """동일 id 중복 → 최고 confidence 만 유지."""
        items = [
            KnowledgeItem(id="K1", key="a", confidence=0.5),
            KnowledgeItem(id="K1", key="a", confidence=0.9),
            KnowledgeItem(id="K1", key="a", confidence=0.3),
        ]
        _dedup_knowledge_items(items)
        assert len(items) == 1
        assert items[0].confidence == 0.9

    def test_different_ids_all_retained(self) -> None:
        """서로 다른 id → 모두 유지."""
        items = [
            KnowledgeItem(id="K1", key="a"),
            KnowledgeItem(id="K2", key="b"),
            KnowledgeItem(id="K3", key="c"),
        ]
        _dedup_knowledge_items(items)
        assert len(items) == 3

    def test_key_whitespace_diff_not_deduped_when_ids_differ(self) -> None:
        """key 표기 차이는 dedup 영향 없음 (id 가 다르면 별개 항목으로 유지).

        N-02 사례 재현 방지: 공백 포함/미포함 key 중복이 발생하더라도
        id 가 달라야 별개로 취급. 같은 id 면 dedup 대상.
        """
        items = [
            KnowledgeItem(id="K1", key="measure:지점 수", confidence=0.5),
            KnowledgeItem(id="K2", key="measure:지점수", confidence=0.9),
        ]
        _dedup_knowledge_items(items)
        assert len(items) == 2

    def test_empty_id_skipped(self) -> None:
        """id 미할당 항목 → dedup 건너뛰어 유지.

        id 미할당은 이론상 발생 안 하지만 방어 로직 검증.
        """
        items = [
            KnowledgeItem(id="", key="ghost"),
            KnowledgeItem(id="K1", key="a"),
        ]
        _dedup_knowledge_items(items)
        # id="" 항목은 best_ki 등록되지 않아 keep_indices 에 포함 안 됨
        assert len(items) == 1
        assert items[0].id == "K1"


# ──────────────────────────────────────────────────────────────────
# merge — key/is_critical 보존 + status 승격
# ──────────────────────────────────────────────────────────────────

class TestMerge:
    def test_preserves_key_and_is_critical(self) -> None:
        """UPDATE 는 기존 key, is_critical 을 절대 덮어쓰지 않는다."""
        existing = [
            KnowledgeItem(
                id="K1", key="measure:지점 수", is_critical=True,
                status=ConfidenceStatus.UNRESOLVED,
            ),
        ]
        updates = [
            KnowledgeItem(
                id="K1", key="", is_critical=False,
                value="COUNT(*)",
                status=ConfidenceStatus.CONFIRMED, confidence=0.9,
            ),
        ]
        _merge_updates_into_items(existing, updates)
        assert existing[0].key == "measure:지점 수"
        assert existing[0].is_critical is True
        assert existing[0].value == "COUNT(*)"
        assert existing[0].status == ConfidenceStatus.CONFIRMED

    def test_promotion_only_upward(self) -> None:
        """status 는 승격 방향으로만 변경, 하향 변경 차단."""
        existing = [
            KnowledgeItem(
                id="K1", key="a", status=ConfidenceStatus.CONFIRMED,
            ),
        ]
        updates = [
            KnowledgeItem(
                id="K1", key="", status=ConfidenceStatus.CANDIDATE,
            ),
        ]
        _merge_updates_into_items(existing, updates)
        assert existing[0].status == ConfidenceStatus.CONFIRMED

    def test_evidence_appended_dedup(self) -> None:
        """evidence 는 중복 없이 누적."""
        existing = [
            KnowledgeItem(id="K1", key="a", evidence=["기존 근거"]),
        ]
        updates = [
            KnowledgeItem(id="K1", key="", evidence=["기존 근거", "신규 근거"]),
        ]
        _merge_updates_into_items(existing, updates)
        assert existing[0].evidence == ["기존 근거", "신규 근거"]

    def test_unknown_id_update_dropped(self) -> None:
        """existing 에 없는 id UPDATE → 방어 로직으로 폐기."""
        existing = [KnowledgeItem(id="K1", key="a")]
        updates = [KnowledgeItem(id="K99", key="", value="x")]
        _merge_updates_into_items(existing, updates)
        # K1 은 변경 없고, K99 는 추가되지 않음
        assert len(existing) == 1
        assert existing[0].id == "K1"
        assert existing[0].value == ""


# ──────────────────────────────────────────────────────────────────
# _should_promote 서열 검증
# ──────────────────────────────────────────────────────────────────

class TestShouldPromote:
    def test_upward(self) -> None:
        assert _should_promote(
            ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFIRMED,
        ) is True
        assert _should_promote(
            ConfidenceStatus.CANDIDATE, ConfidenceStatus.PROBABLE,
        ) is True

    def test_same_or_downward(self) -> None:
        assert _should_promote(
            ConfidenceStatus.CONFIRMED, ConfidenceStatus.CONFIRMED,
        ) is False
        assert _should_promote(
            ConfidenceStatus.PROBABLE, ConfidenceStatus.CANDIDATE,
        ) is False


# ──────────────────────────────────────────────────────────────────
# DeadEnd — related_knowledge_ids 필드명
# ──────────────────────────────────────────────────────────────────

class TestDeadEndFieldRename:
    def test_dead_end_uses_ids_not_keys(self) -> None:
        """DeadEnd.related_knowledge_ids 로 id 를 기록 (N-02 사례 방지).

        id 기반 추적이어야 key 표기 차이(공백 등)로 매칭이 깨지지 않는다.
        """
        de = DeadEnd(
            hypothesis_id="H1",
            failure_type=FailureType.NO_KNOWLEDGE,
            reason="test",
            related_knowledge_ids=["K1", "K2"],
        )
        assert de.related_knowledge_ids == ["K1", "K2"]

    def test_dead_end_default_empty(self) -> None:
        de = DeadEnd(
            hypothesis_id="H1",
            failure_type=FailureType.NO_KNOWLEDGE,
            reason="test",
        )
        assert de.related_knowledge_ids == []
