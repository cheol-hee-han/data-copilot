-- ============================================================
-- 파일: resources/queries/search_similar_sql.sql
-- 용도: SQL 이력 DB에서 유사 과거 SQL 키워드 검색
-- 대상: customization-targets.md (이력 검색 SQL 커스터마이징)
-- ============================================================
-- {conditions} — 키워드별 ILIKE 조건이 동적 삽입됨
--   예: query_text ILIKE :kw0 OR query_text ILIKE :kw1
-- 폐쇄망 전환 시: ILIKE → LIKE (Sybase IQ/Impala)
-- ============================================================
SELECT query_text,
       sql,
       executed_at,
       success
  FROM sql_query_history
 WHERE success = TRUE
   AND ({conditions})
 ORDER BY executed_at DESC
 LIMIT 5
