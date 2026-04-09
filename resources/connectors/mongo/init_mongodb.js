/**
 * MongoDB 컬렉션 스키마 초기화 스크립트
 *
 * 대상 DB: meta_db
 * 컬렉션 5개:
 *   1. dpasset_table       — 테이블 메타 (query_table_meta.json 메인)
 *   2. dpasset_column      — 컬럼 메타 (query_table_meta.json $lookup 대상)
 *   3. standard_code       — 코드 메타 (query_code_meta.json 메인)
 *   4. standard_code_value — 코드값 (query_code_meta.json $lookup 대상)
 *   5. biz_term            — 업무 용어사전 (query_dictionary.json 메인)
 *
 * 실행:
 *   docker exec dc-mongodb mongosh -u mongoadmin -p mongo_pass \
 *     --authenticationDatabase admin meta_db /scripts/init_mongodb.js
 *
 * 또는 로컬:
 *   mongosh "mongodb://mongoadmin:mongo_pass@localhost:27017/meta_db?authSource=admin" \
 *     --file resources/mongodb/init_mongodb.js
 */

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// DB 선택
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
use("meta_db");

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 1. dpasset_table — 테이블 메타
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// query_table_meta.json 메인 컬렉션
// 필드: schema_name, name, alt_name, desc
// $lookup 시 localField: name → dpasset_column.table_name

db.createCollection("dpasset_table", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["name"],
      properties: {
        schema_name: {
          bsonType: "string",
          description: "스키마명 (예: info_db, hist_db)"
        },
        name: {
          bsonType: "string",
          description: "테이블 물리명 (예: TB_CUST_INFO)"
        },
        alt_name: {
          bsonType: "string",
          description: "테이블 한글명 (예: 고객기본정보)"
        },
        desc: {
          bsonType: "string",
          description: "테이블 설명"
        }
      }
    }
  }
});

db.dpasset_table.createIndex({ name: 1 }, { unique: true });
db.dpasset_table.createIndex({ alt_name: "text", desc: "text" }, { name: "idx_table_text" });

print("  [OK] dpasset_table 컬렉션 생성 완료");

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 2. dpasset_column — 컬럼 메타
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// query_table_meta.json $lookup 대상
// foreignField: table_name (dpasset_table.name과 매칭)
// 필드: name, alt_name, data_type, desc, pk, table_name

db.createCollection("dpasset_column", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["name", "table_name"],
      properties: {
        table_name: {
          bsonType: "string",
          description: "소속 테이블 물리명 (FK → dpasset_table.name)"
        },
        name: {
          bsonType: "string",
          description: "컬럼 물리명 (예: CUST_NO)"
        },
        alt_name: {
          bsonType: "string",
          description: "컬럼 한글명 (예: 고객번호)"
        },
        data_type: {
          bsonType: "string",
          description: "데이터 타입 (예: VARCHAR, NUMBER, DATE)"
        },
        desc: {
          bsonType: "string",
          description: "컬럼 설명"
        },
        pk: {
          bsonType: "bool",
          description: "기본키 여부"
        }
      }
    }
  }
});

db.dpasset_column.createIndex({ table_name: 1, name: 1 }, { unique: true });
db.dpasset_column.createIndex({ table_name: 1 }, { name: "idx_column_table" });

print("  [OK] dpasset_column 컬렉션 생성 완료");

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 3. standard_code — 코드 메타
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// query_code_meta.json 메인 컬렉션
// 필드: name, alt_name
// $lookup 시 localField: _id → standard_code_value.code_id

db.createCollection("standard_code", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["name"],
      properties: {
        name: {
          bsonType: "string",
          description: "코드 물리명 (예: STATUS_CD)"
        },
        alt_name: {
          bsonType: "string",
          description: "코드 한글명 (예: 상태코드)"
        }
      }
    }
  }
});

db.standard_code.createIndex({ name: 1 }, { unique: true });

print("  [OK] standard_code 컬렉션 생성 완료");

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 4. standard_code_value — 코드값
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// query_code_meta.json $lookup 대상
// foreignField: code_id (standard_code._id와 매칭)
// 필드: code_id, code_value, code_name

db.createCollection("standard_code_value", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["code_id", "code_value"],
      properties: {
        code_id: {
          bsonType: "objectId",
          description: "소속 코드 ID (FK → standard_code._id)"
        },
        code_value: {
          bsonType: "string",
          description: "코드값 (예: 01, 02, 09)"
        },
        code_name: {
          bsonType: "string",
          description: "코드값 한글명 (예: 정상, 휴면, 해지)"
        }
      }
    }
  }
});

db.standard_code_value.createIndex({ code_id: 1, code_value: 1 }, { unique: true });
db.standard_code_value.createIndex({ code_id: 1 }, { name: "idx_code_value_code_id" });

print("  [OK] standard_code_value 컬렉션 생성 완료");

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 5. biz_term — 업무 용어사전
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// query_dictionary.json 메인 컬렉션
// 필드: name, synonyms, biz_term_definition, table_ids
// $lookup 시 localField: table_ids → dpasset_table._id

db.createCollection("biz_term", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["name"],
      properties: {
        name: {
          bsonType: "string",
          description: "용어명 (예: 여신잔액)"
        },
        synonyms: {
          bsonType: "array",
          items: { bsonType: "string" },
          description: "동의어 목록 (예: [대출잔액, 융자잔액])"
        },
        biz_term_definition: {
          bsonType: "string",
          description: "용어 정의 (예: 대출 실행 후 미상환 원금 잔액)"
        },
        table_ids: {
          bsonType: "array",
          items: { bsonType: "objectId" },
          description: "관련 테이블 ID 목록 (FK → dpasset_table._id)"
        }
      }
    }
  }
});

db.biz_term.createIndex({ name: 1 }, { unique: true });
db.biz_term.createIndex({ synonyms: 1 }, { name: "idx_biz_term_synonyms" });
db.biz_term.createIndex({ name: "text", biz_term_definition: "text" }, { name: "idx_biz_term_text" });

print("  [OK] biz_term 컬렉션 생성 완료");

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 완료 확인
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

print("\n=== MongoDB 컬렉션 초기화 완료 ===");
print("컬렉션 목록:");
db.getCollectionNames().forEach(function(name) {
  var count = db.getCollection(name).countDocuments();
  print("  - " + name + " (" + count + "건)");
});

print("\n관계 구조:");
print("  dpasset_table.name ──1:N──→ dpasset_column.table_name");
print("  standard_code._id  ──1:N──→ standard_code_value.code_id");
print("  biz_term.table_ids ──N:M──→ dpasset_table._id");
