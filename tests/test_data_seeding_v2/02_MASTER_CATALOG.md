# 02. 마스터 테이블 카탈로그 (v2) — 자동 생성

**1,441 테이블** 전체 인벤토리 (이름 + 한글명).
catalog_v2.json에서 자동 재생성 (2026-04-22T13:28).
상세 컬럼 정의는 주제영역별 파일(`10_*` ~ `C8_*`)에서 확인.

**유형코드:** M(Master) L(Log거래) H(History이력) S(Summary집계) P(snaP샷) C(Code) T(Task작업) G(loG) D(Detail)

---

## 1. 공통·조직·코드·시스템 (50)

### CMI (30)

- `CMI001M` COM_부점기본
- `CMI002H` COM_부점변경이력
- `CMI003M` COM_부점계층
- `CMI004M` COM_부점그룹
- `CMI005M` COM_부점그룹매핑
- `CMI006H` COM_부점통폐합이력
- `CMI007M` COM_직원기본
- `CMI008H` COM_직원발령이력
- `CMI009M` COM_직원재직현황
- `CMI010H` COM_직원승진이력
- `CMI011M` COM_직원자격증
- `CMI012M` COM_팀구성
- `CMI013H` COM_팀이동이력
- `CMI014M` COM_임원기본
- `CMI015H` COM_임원임기이력
- `CMI016M` COM_직위직책매핑
- `CMI017H` COM_부점장이력
- `CMI018M` COM_외주인력
- `CMI019M` COM_조직도스냅샷
- `CMI020H` COM_조직개편이력
- `CMI021C` COM_영업일코드
- `CMI022C` COM_공휴일코드
- `CMI023C` COM_외환영업일
- `CMI024C` COM_한국은행영업일
- `CMI025M` COM_코드마스터
- `CMI026C` COM_코드상세
- `CMI027H` COM_코드변경이력
- `CMI028M` COM_코드영문명
- `CMI029M` COM_화폐단위
- `CMI030M` COM_지역코드

### CMO (10)

- `CMO001M` ORG_권한그룹
- `CMO002M` ORG_역할
- `CMO003M` ORG_역할권한매핑
- `CMO004M` ORG_직원역할
- `CMO005H` ORG_권한변경이력
- `CMO006L` ORG_접근로그
- `CMO007M` ORG_메뉴
- `CMO008M` ORG_메뉴권한
- `CMO009M` ORG_업무구분
- `CMO010M` ORG_승인자지정

### CMS (10)

- `CMS001M` SYS_시스템기본
- `CMS002M` SYS_ETL잡마스터
- `CMS003L` SYS_ETL실행로그
- `CMS004L` SYS_ETL에러로그
- `CMS005M` SYS_테이블메타
- `CMS006M` SYS_컬럼메타
- `CMS007M` SYS_도메인사전
- `CMS008M` SYS_시스템파라미터
- `CMS009H` SYS_파라미터변경이력
- `CMS010M` SYS_잡스케줄


## 2. 고객·CIF·신용평가 (81)

### CSC (36)

- `CSC001M` CSC_고객기본
- `CSC002M` CSC_고객연락처
- `CSC003M` CSC_고객주소
- `CSC004M` CSC_고객직업
- `CSC005M` CSC_기업고객
- `CSC006M` CSC_기업재무
- `CSC007M` CSC_기업관계자
- `CSC008M` CSC_고객관계
- `CSC009H` CSC_고객기본이력
- `CSC010H` CSC_고객연락처이력
- `CSC011H` CSC_고객주소이력
- `CSC012H` CSC_고객직업이력
- `CSC013M` CSC_개인사업자
- `CSC014M` CSC_외국인고객
- `CSC015M` CSC_재외국민
- `CSC016M` CSC_미성년자
- `CSC017M` CSC_단체고객
- `CSC018M` CSC_정부공공고객
- `CSC019M` CSC_고객금융성향
- `CSC020M` CSC_고객성별연령구간
- `CSC021M` CSC_고객수신동의
- `CSC022M` CSC_고객개인정보동의
- `CSC023H` CSC_수신동의이력
- `CSC024M` CSC_간편인증정보
- `CSC025M` CSC_전자지갑
- `CSC026M` CSC_고객세그먼트
- `CSC027H` CSC_세그먼트이력
- `CSC028M` CSC_VIP등급
- `CSC029H` CSC_VIP등급이력
- `CSC030M` CSC_우수고객선정
- `CSC031M` CSC_휴면고객
- `CSC032H` CSC_휴면편입해제이력
- `CSC033M` CSC_장기미거래고객
- `CSC034M` CSC_자동이체주거래
- `CSC035M` CSC_채널선호도
- `CSC036L` CSC_고객통합이력

### CSI (30)

- `CSI001M` CSI_고객신용등급
- `CSI002H` CSI_고객신용등급이력
- `CSI003M` CSI_신용평가
- `CSI004M` CSI_행내신용점수
- `CSI005M` CSI_CB신용점수
- `CSI006H` CSI_CB점수이력
- `CSI007M` CSI_KCB연계
- `CSI008M` CSI_NICE연계
- `CSI009M` CSI_금감원신용정보
- `CSI010M` CSI_신용정보원연계
- `CSI011M` CSI_기업신용등급
- `CSI012H` CSI_기업신용등급이력
- `CSI013M` CSI_연체정보
- `CSI014M` CSI_채무불이행
- `CSI015M` CSI_외부부도정보
- `CSI016M` CSI_신용회복
- `CSI017M` CSI_개인회생파산
- `CSI018M` CSI_피성년후견
- `CSI019M` CSI_금융거래제한자
- `CSI020M` CSI_외부차입현황
- `CSI021M` CSI_외부카드현황
- `CSI022M` CSI_DSR
- `CSI023M` CSI_DTI
- `CSI024M` CSI_LTI
- `CSI025M` CSI_소득정보
- `CSI026M` CSI_재직증빙
- `CSI027M` CSI_자산현황
- `CSI028M` CSI_부채현황
- `CSI029H` CSI_DSR이력
- `CSI030M` CSI_연체추심상태

### CSK (15)

- `CSK001M` CSK_고객위험도
- `CSK002H` CSK_고객위험도이력
- `CSK003M` CSK_CDD
- `CSK004M` CSK_EDD
- `CSK005M` CSK_실제소유자
- `CSK006M` CSK_정치적주요인물
- `CSK007M` CSK_제재대상매칭
- `CSK008L` CSK_STR
- `CSK009L` CSK_CTR
- `CSK010M` CSK_재심사일정
- `CSK011H` CSK_정보변경이력
- `CSK012M` CSK_원천자금조사
- `CSK013M` CSK_외국인고객심사
- `CSK014M` CSK_비거주자고객
- `CSK015M` CSK_고객면담기록


## 3. 상품·약관·금리 (55)

### PFP (20)

- `PFP001M` PRD_상품기본
- `PFP002M` PRD_상품분류
- `PFP003M` PRD_수신상품
- `PFP004M` PRD_여신상품
- `PFP005M` PRD_카드상품
- `PFP006M` PRD_외환상품
- `PFP007M` PRD_신탁상품
- `PFP008M` PRD_퇴직연금상품
- `PFP009M` PRD_펀드상품
- `PFP010M` PRD_ISA상품
- `PFP011M` PRD_상품판매채널
- `PFP012M` PRD_상품권장세그먼트
- `PFP013M` PRD_상품브로셔
- `PFP014M` PRD_상품버전
- `PFP015H` PRD_상품변경이력
- `PFP016M` PRD_상품판매실적
- `PFP017M` PRD_상품우대금리
- `PFP018M` PRD_타행상품비교
- `PFP019M` PRD_전략상품지정
- `PFP020M` PRD_신상품개발

### PFR (15)

- `PFR001M` PRT_약관기본
- `PFR002M` PRT_약관버전
- `PFR003M` PRT_약관동의
- `PFR004H` PRT_약관변경이력
- `PFR005M` PRT_가입제한
- `PFR006M` PRT_취급한도
- `PFR007M` PRT_판매기간
- `PFR008M` PRT_리베이트정책
- `PFR009M` PRT_세제혜택
- `PFR010M` PRT_예보대상
- `PFR011M` PRT_상품판매중지
- `PFR012M` PRT_특약조건
- `PFR013M` PRT_판매관리점
- `PFR014M` PRT_상품승인현황
- `PFR015M` PRT_법률검토

### PFC (20)

- `PFC001M` PFE_상품금리
- `PFC002H` PFE_상품금리이력
- `PFC003M` PFE_상품수수료
- `PFC004H` PFE_상품수수료이력
- `PFC005M` PFE_기준금리
- `PFC006H` PFE_기준금리이력
- `PFC007M` PFE_우대조건
- `PFC008M` PFE_우대조건적용
- `PFC009M` PFE_금리구간
- `PFC010M` PFE_금리정책
- `PFC011M` PFE_수수료정책
- `PFC012M` PFE_수수료면제
- `PFC013M` PFE_중도해지공제
- `PFC014M` PFE_가산금리
- `PFC015M` PFE_일일금리고시
- `PFC016M` PFE_자동이체금리
- `PFC017M` PFE_급여이체우대
- `PFC018M` PFE_카드실적우대
- `PFC019M` PFE_복합상품우대
- `PFC020M` PFE_청년우대


## 4. 수신 (146)

### DPG (31)

- `DPG001M` DPG_계좌기본공통
- `DPG002M` DPG_계약조건공통
- `DPG003M` DPG_공동명의
- `DPG004M` DPG_질권압류
- `DPG005M` DPG_상속지급
- `DPG006M` DPG_자동이체약정
- `DPG007L` DPG_자동이체실행
- `DPG008H` DPG_상태이력공통
- `DPG009M` DPG_만기관리
- `DPG010M` DPG_만기자동연장
- `DPG011L` DPG_이자지급
- `DPG012L` DPG_세금원천징수
- `DPG013M` DPG_편의서비스
- `DPG014L` DPG_입출금알림
- `DPG015M` DPG_급여이체등록
- `DPG016L` DPG_급여이체실적
- `DPG017M` DPG_휴면예금
- `DPG018L` DPG_휴면편입
- `DPG019L` DPG_휴면해제
- `DPG020M` DPG_실명확인
- `DPG021M` DPG_예금자보호가입현황
- `DPG022L` DPG_긴급생활자금
- `DPG023M` DPG_사고예금
- `DPG024L` DPG_사고처리
- `DPG025M` DPG_통장재발급
- `DPG026M` DPG_OTP연계
- `DPG027L` DPG_공동인증서연결
- `DPG028M` DPG_소액대여한도
- `DPG029L` DPG_비밀번호변경
- `DPG030L` DPG_통장개설
- `DPG031H` DPG_계좌명의변경이력

### DPF (25)

- `DPF001M` DPF_정기예금기본
- `DPF002M` DPF_정기예금계약
- `DPF003P` DPF_정기예금일별잔액
- `DPF004P` DPF_정기예금월말잔액
- `DPF005L` DPF_정기예금거래
- `DPF006H` DPF_정기예금상태이력
- `DPF007L` DPF_정기예금이자지급
- `DPF008M` DPF_정기예금만기현황
- `DPF009L` DPF_정기예금중도해지
- `DPF010L` DPF_정기예금자동연장
- `DPF011M` DPF_정기예금우대현황
- `DPF012H` DPF_정기예금금리이력
- `DPF013M` DPF_목돈정기예금
- `DPF014M` DPF_실버정기예금
- `DPF015M` DPF_청년정기예금
- `DPF016M` DPF_기업정기예금
- `DPF017M` DPF_복리정기예금
- `DPF018M` DPF_실세금리정기예금
- `DPF019M` DPF_양도성예금증서
- `DPF020L` DPF_CD발행
- `DPF021M` DPF_단기표지어음
- `DPF022L` DPF_표지어음발행
- `DPF023M` DPF_특정상품가입현황
- `DPF024H` DPF_특정상품가입이력
- `DPF025M` DPF_세금우대예금

### DPD (30)

- `DPD001M` DPD_정기적금기본
- `DPD002M` DPD_정기적금계약
- `DPD003P` DPD_정기적금일별잔액
- `DPD004P` DPD_정기적금월말잔액
- `DPD005L` DPD_정기적금납입
- `DPD006L` DPD_정기적금거래
- `DPD007H` DPD_정기적금상태이력
- `DPD008L` DPD_정기적금이자지급
- `DPD009M` DPD_정기적금만기현황
- `DPD010L` DPD_정기적금중도해지
- `DPD011L` DPD_정기적금납입실패
- `DPD012M` DPD_정기적금우대현황
- `DPD013M` DPD_청년희망적금
- `DPD014M` DPD_청년도약계좌
- `DPD015H` DPD_정기적금금리이력
- `DPD016M` DPD_자유적금기본
- `DPD017M` DPD_자유적금계약
- `DPD018P` DPD_자유적금일별잔액
- `DPD019L` DPD_자유적금납입
- `DPD020L` DPD_자유적금이자지급
- `DPD021M` DPD_주택청약저축기본
- `DPD022M` DPD_청약순위
- `DPD023M` DPD_청약가점
- `DPD024L` DPD_청약참여
- `DPD025L` DPD_청약당첨
- `DPD026L` DPD_청약취소
- `DPD027L` DPD_청약납입
- `DPD028M` DPD_청약결합상품
- `DPD029L` DPD_청약이자지급
- `DPD030H` DPD_청약상태이력

### DPB (20)

- `DPB001M` DPB_요구불기본
- `DPB002M` DPB_보통예금
- `DPB003M` DPB_저축예금
- `DPB004P` DPB_요구불일별잔액
- `DPB005P` DPB_요구불월말잔액
- `DPB006L` DPB_요구불거래
- `DPB007L` DPB_이체거래
- `DPB008L` DPB_예약이체
- `DPB009L` DPB_요구불이자지급
- `DPB010M` DPB_MMDA기본
- `DPB011M` DPB_당좌예금
- `DPB012L` DPB_당좌수표발행
- `DPB013L` DPB_당좌어음발행
- `DPB014L` DPB_부도이력
- `DPB015M` DPB_카드연계계좌
- `DPB016M` DPB_가상계좌
- `DPB017L` DPB_가상계좌입금
- `DPB018M` DPB_별단예금
- `DPB019M` DPB_ATM한도
- `DPB020H` DPB_한도변경이력

### DPN (20)

- `DPN001M` DPN_신탁기본
- `DPN002M` DPN_신탁약정
- `DPN003M` DPN_신탁운용자산
- `DPN004P` DPN_신탁일별기준가
- `DPN005L` DPN_신탁거래
- `DPN006M` DPN_신탁수익률
- `DPN007L` DPN_신탁수수료
- `DPN008M` DPN_특정금전신탁
- `DPN009M` DPN_불특정금전신탁
- `DPN010M` DPN_퇴직신탁
- `DPN011M` DPN_연금신탁
- `DPN012L` DPN_신탁수익지급
- `DPN013L` DPN_신탁해지
- `DPN014M` DPN_신탁기초자산
- `DPN015L` DPN_운용지시이력
- `DPN016M` DPN_신탁보고서
- `DPN017M` DPN_ELT
- `DPN018M` DPN_원금보장신탁
- `DPN019H` DPN_신탁상태이력
- `DPN020M` DPN_신탁통지동의

### DPY (20)

- `DPY001M` DPY_외화예금기본
- `DPY002M` DPY_외화보통예금
- `DPY003M` DPY_외화정기예금
- `DPY004M` DPY_외화적금
- `DPY005P` DPY_외화일별잔액
- `DPY006P` DPY_외화월말잔액
- `DPY007L` DPY_외화거래
- `DPY008L` DPY_외화이자지급
- `DPY009L` DPY_환전거래
- `DPY010L` DPY_외화송금
- `DPY011M` DPY_환율
- `DPY012P` DPY_외화재평가
- `DPY013M` DPY_외화MMDA
- `DPY014M` DPY_비거주자계좌
- `DPY015L` DPY_외환신고
- `DPY016M` DPY_해외이주예금
- `DPY017M` DPY_유학예금
- `DPY018M` DPY_통화통합관리
- `DPY019M` DPY_외화자동이체
- `DPY020H` DPY_외화계좌상태이력


## 5. 여신 (211)

### LNB (41)

- `LNB001M` LNB_대출기본
- `LNB002M` LNB_대출약정
- `LNB003P` LNB_대출일별잔액
- `LNB004P` LNB_대출월말잔액
- `LNB005L` LNB_대출실행
- `LNB006M` LNB_대출한도
- `LNB007H` LNB_한도변경이력
- `LNB008M` LNB_연대보증인
- `LNB009L` LNB_원리금납입
- `LNB010L` LNB_중도상환
- `LNB011L` LNB_여신신청
- `LNB012L` LNB_여신심사
- `LNB013H` LNB_상태이력
- `LNB014H` LNB_금리이력
- `LNB015M` LNB_상환스케줄
- `LNB016M` LNB_거치전환
- `LNB017L` LNB_만기연장
- `LNB018M` LNB_우대금리
- `LNB019M` LNB_대출용도
- `LNB020L` LNB_이자부과
- `LNB021L` LNB_수수료부과
- `LNB022M` LNB_담보연계
- `LNB023L` LNB_대환
- `LNB024L` LNB_기한이익상실
- `LNB025L` LNB_대위변제
- `LNB026M` LNB_조기경보
- `LNB027L` LNB_조기경보조치
- `LNB028M` LNB_여신건전성
- `LNB029H` LNB_건전성변동이력
- `LNB030L` LNB_상각
- `LNB031L` LNB_상각후회수
- `LNB032L` LNB_채권양도
- `LNB033M` LNB_채권관리인
- `LNB034M` LNB_대출상환유예
- `LNB035L` LNB_금리변경요청
- `LNB036L` LNB_조건변경
- `LNB037M` LNB_여신잔액세그먼트
- `LNB038M` LNB_담당자배정
- `LNB039L` LNB_통지발송
- `LNB040L` LNB_민원처리
- `LNB041M` LNB_대출실행조건

### LNH (30)

- `LNH001M` LNH_주담대기본
- `LNH002M` LNH_담보부동산
- `LNH003M` LNH_감정평가
- `LNH004M` LNH_LTV이력
- `LNH005M` LNH_실거주확인
- `LNH006M` LNH_보금자리론
- `LNH007M` LNH_적격대출
- `LNH008M` LNH_금리혼합형
- `LNH009M` LNH_주담대부실위험
- `LNH010M` LNH_보유주택
- `LNH011L` LNH_규제한도변경이력
- `LNH012M` LNH_주택가격조회
- `LNH013M` LNH_생활안정자금
- `LNH014M` LNH_분양잔금대출
- `LNH015M` LNH_집단대출
- `LNH016L` LNH_주담대심사상세
- `LNH017M` LNH_근저당설정
- `LNH018L` LNH_근저당말소
- `LNH019L` LNH_소유권변동
- `LNH020M` LNH_임차인확인
- `LNH021L` LNH_경매진행
- `LNH022L` LNH_공매진행
- `LNH023M` LNH_MCI
- `LNH024M` LNH_MCG
- `LNH025M` LNH_담보훼손
- `LNH026M` LNH_화재보험
- `LNH027L` LNH_임대차실사
- `LNH028M` LNH_재건축포함
- `LNH029L` LNH_보유주택처분
- `LNH030M` LNH_중도금대출

### LNJ (20)

- `LNJ001M` LNJ_전세자금대출기본
- `LNJ002M` LNJ_임대차계약
- `LNJ003M` LNJ_임대인정보
- `LNJ004L` LNJ_전세실행
- `LNJ005M` LNJ_버팀목대출
- `LNJ006M` LNJ_청년전세
- `LNJ007M` LNJ_중소기업청년
- `LNJ008M` LNJ_전세보증금반환보증
- `LNJ009M` LNJ_임대차만기관리
- `LNJ010L` LNJ_임차인퇴거
- `LNJ011L` LNJ_임대인체납신고
- `LNJ012M` LNJ_전세보증반환사고
- `LNJ013M` LNJ_임대인위험평가
- `LNJ014M` LNJ_전입신고
- `LNJ015L` LNJ_임대차연장신청
- `LNJ016M` LNJ_임차권등기
- `LNJ017M` LNJ_전세가격조회
- `LNJ018L` LNJ_전세사기신고
- `LNJ019M` LNJ_전세이체
- `LNJ020H` LNJ_전세대출상태이력

### LNC (25)

- `LNC001M` LNC_신용대출기본
- `LNC002M` LNC_직장인신용대출
- `LNC003M` LNC_공무원대출
- `LNC004M` LNC_전문직대출
- `LNC005M` LNC_마이너스통장
- `LNC006L` LNC_마이너스이용
- `LNC007M` LNC_비상금대출
- `LNC008L` LNC_직장변경신고
- `LNC009M` LNC_소득재확인
- `LNC010M` LNC_재직증빙
- `LNC011M` LNC_소속회사연계
- `LNC012M` LNC_모집인연계
- `LNC013M` LNC_신용대출한도관리
- `LNC014M` LNC_햇살론
- `LNC015M` LNC_미소금융
- `LNC016M` LNC_새희망홀씨
- `LNC017M` LNC_사잇돌대출
- `LNC018M` LNC_근로자대출
- `LNC019M` LNC_통합지원대출
- `LNC020L` LNC_대출비교공시
- `LNC021M` LNC_개인회생대출관리
- `LNC022M` LNC_채무조정
- `LNC023L` LNC_금리인하요구실적
- `LNC024M` LNC_신용대출연체예측
- `LNC025M` LNC_신용대출고객군분류

### LNK (40)

- `LNK001M` LNK_기업여신기본
- `LNK002M` LNK_운영자금대출
- `LNK003M` LNK_시설자금대출
- `LNK004M` LNK_기업당좌대출
- `LNK005M` LNK_어음할인
- `LNK006M` LNK_부동산PF
- `LNK007M` LNK_무역금융
- `LNK008M` LNK_재무제표연계
- `LNK009M` LNK_기업신용평가
- `LNK010M` LNK_업종별한도
- `LNK011M` LNK_차주그룹
- `LNK012M` LNK_그룹여신한도
- `LNK013M` LNK_대주주연대보증
- `LNK014L` LNK_기업여신실행
- `LNK015M` LNK_기업여신한도한도변경
- `LNK016M` LNK_기술신용평가
- `LNK017M` LNK_보증기관연계
- `LNK018M` LNK_벤처이노비즈기업
- `LNK019M` LNK_기업여신건전성
- `LNK020L` LNK_여신위원회심의
- `LNK021M` LNK_기업어음매입
- `LNK022M` LNK_회사채인수
- `LNK023M` LNK_정책자금연계
- `LNK024M` LNK_수출환어음
- `LNK025M` LNK_수입화환
- `LNK026M` LNK_무역보험
- `LNK027M` LNK_기업지급보증
- `LNK028L` LNK_지급보증이행
- `LNK029M` LNK_매출채권팩토링
- `LNK030M` LNK_전자채권
- `LNK031M` LNK_법인카드여신
- `LNK032M` LNK_산업은행연계
- `LNK033M` LNK_지역보증연계
- `LNK034M` LNK_상업어음
- `LNK035M` LNK_기업대출약정서
- `LNK036M` LNK_재무약정위반
- `LNK037M` LNK_기업여신건의
- `LNK038L` LNK_기업RM활동
- `LNK039M` LNK_기업할인어음만기
- `LNK040H` LNK_기업여신상태이력

### LNW (25)

- `LNW001M` LNW_정책자금기본
- `LNW002M` LNW_소상공인자금
- `LNW003M` LNW_중진공자금
- `LNW004M` LNW_청년창업자금
- `LNW005M` LNW_재창업자금
- `LNW006M` LNW_코로나경영안정
- `LNW007M` LNW_새출발기금
- `LNW008M` LNW_일자리자금
- `LNW009M` LNW_농어업자금
- `LNW010M` LNW_이자보전
- `LNW011M` LNW_정책대출용도점검
- `LNW012M` LNW_특별재난자금
- `LNW013M` LNW_여성창업
- `LNW014M` LNW_장애인기업자금
- `LNW015M` LNW_북한이탈주민
- `LNW016M` LNW_혁신기업자금
- `LNW017M` LNW_그린뉴딜자금
- `LNW018M` LNW_수출자금
- `LNW019M` LNW_공공조달
- `LNW020M` LNW_희망회복자금
- `LNW021M` LNW_프랜차이즈
- `LNW022M` LNW_전통시장
- `LNW023L` LNW_정책자금정산
- `LNW024M` LNW_정책보증
- `LNW025H` LNW_정책자금상태이력

### LNO (30)

- `LNO001M` LNO_연체기본
- `LNO002L` LNO_연체이자부과
- `LNO003L` LNO_독촉통지
- `LNO004L` LNO_추심활동
- `LNO005M` LNO_연체재입금약정
- `LNO006L` LNO_법적조치
- `LNO007L` LNO_연체회수
- `LNO008M` LNO_회생절차
- `LNO009M` LNO_파산절차
- `LNO010M` LNO_워크아웃
- `LNO011L` LNO_추심위탁
- `LNO012M` LNO_연체재약정
- `LNO013H` LNO_연체등급이력
- `LNO014L` LNO_연체해소
- `LNO015M` LNO_신용회복지원
- `LNO016M` LNO_연체분석집계
- `LNO017M` LNO_부실예측
- `LNO018M` LNO_연체이유분류
- `LNO019M` LNO_집중관리대상
- `LNO020M` LNO_회수성과
- `LNO021M` LNO_한계차주
- `LNO022M` LNO_NPL관리
- `LNO023L` LNO_NPL매각
- `LNO024M` LNO_충당금적립
- `LNO025L` LNO_환입
- `LNO026M` LNO_동일차주다건연체
- `LNO027L` LNO_상각결의
- `LNO028L` LNO_채권추심행동
- `LNO029M` LNO_연체고객블랙리스트
- `LNO030M` LNO_RoM관리결과


## 6. 담보·보증 (46)

### LNM (31)

- `LNM001M` LNM_담보기본
- `LNM002M` LNM_부동산담보
- `LNM003M` LNM_금융상품담보
- `LNM004M` LNM_매출채권담보
- `LNM005M` LNM_담보평가이력
- `LNM006M` LNM_감정평가기관
- `LNM007L` LNM_담보설정
- `LNM008L` LNM_담보해제
- `LNM009M` LNM_담보소유권변동
- `LNM010L` LNM_담보처분
- `LNM011M` LNM_공동담보
- `LNM012M` LNM_포괄근담보
- `LNM013M` LNM_담보훼손관리
- `LNM014M` LNM_담보보험
- `LNM015H` LNM_담보상태이력
- `LNM016M` LNM_부동산시세조회
- `LNM017L` LNM_현장조사
- `LNM018M` LNM_담보위험등급
- `LNM019M` LNM_담보추가요청
- `LNM020L` LNM_담보교체
- `LNM021M` LNM_담보위임
- `LNM022M` LNM_감정평가수수료
- `LNM023M` LNM_담보경매
- `LNM024M` LNM_임의매각
- `LNM025M` LNM_전세임대인담보
- `LNM026M` LNM_담보정기점검
- `LNM027M` LNM_등기부조회이력
- `LNM028M` LNM_담보LTV준수
- `LNM029L` LNM_담보문서보관
- `LNM030M` LNM_담보총괄지표
- `LNM031M` LNM_외부담보권자

### LNG (15)

- `LNG001M` LNG_보증기본
- `LNG002M` LNG_보증인정보
- `LNG003M` LNG_HF주택금융공사보증
- `LNG004M` LNG_HUG주택도시보증
- `LNG005M` LNG_SGIC보증
- `LNG006M` LNG_신보기보보증
- `LNG007L` LNG_보증료지급
- `LNG008L` LNG_보증이행청구
- `LNG009M` LNG_구상권관리
- `LNG010M` LNG_보증인신용
- `LNG011L` LNG_보증해제
- `LNG012M` LNG_보증한도관리
- `LNG013M` LNG_사채보증
- `LNG014M` LNG_무역보증
- `LNG015H` LNG_보증상태이력


## 7. 카드 회원 (50)

- `CLN001M` CLN_카드회원
- `CLN002M` CLN_카드기본
- `CLN003M` CLN_카드상품
- `CLN004L` CLN_카드신청
- `CLN005L` CLN_카드심사
- `CLN006L` CLN_카드재발급
- `CLN007M` CLN_가족카드
- `CLN008M` CLN_법인카드
- `CLN009M` CLN_카드한도
- `CLN010H` CLN_한도변경이력
- `CLN011M` CLN_카드혜택
- `CLN012M` CLN_포인트잔액
- `CLN013L` CLN_포인트거래
- `CLN014M` CLN_자동결제설정
- `CLN015M` CLN_카드탈회
- `CLN016L` CLN_분실도난신고
- `CLN017L` CLN_부정사용보고
- `CLN018M` CLN_회원등급평가
- `CLN019M` CLN_마케팅동의
- `CLN020L` CLN_카드상태변경
- `CLN021M` CLN_연회비
- `CLN022M` CLN_카드수령
- `CLN023M` CLN_카드비밀번호
- `CLN024M` CLN_카드모집인
- `CLN025M` CLN_카드민원
- `CLN026M` CLN_현금서비스
- `CLN027L` CLN_현금서비스이용
- `CLN028M` CLN_카드론
- `CLN029M` CLN_리볼빙
- `CLN030M` CLN_할부약정
- `CLN031L` CLN_할부상환
- `CLN032M` CLN_월별청구
- `CLN033L` CLN_결제이체
- `CLN034M` CLN_카드연체
- `CLN035L` CLN_해외이용
- `CLN036M` CLN_부가서비스
- `CLN037M` CLN_캐시백
- `CLN038M` CLN_이용실적월별
- `CLN039M` CLN_이용실적연간
- `CLN040L` CLN_선결제
- `CLN041L` CLN_부분취소
- `CLN042M` CLN_할부전환
- `CLN043M` CLN_정기결제
- `CLN044L` CLN_포인트전환
- `CLN045M` CLN_결제약정
- `CLN046L` CLN_현금서비스이자
- `CLN047L` CLN_카드재결제요청
- `CLN048M` CLN_체크카드출금
- `CLN049M` CLN_BIN관리
- `CLN050M` CLN_카드성과월말


## 8. 카드 매출·정산 (70)

- `SLE001L` SLE_카드매출
- `SLE002L` SLE_카드승인
- `SLE003L` SLE_일시불매출
- `SLE004L` SLE_할부매출
- `SLE005L` SLE_현금서비스매출
- `SLE006L` SLE_체크카드매출
- `SLE007L` SLE_해외매출
- `SLE008L` SLE_매입전문
- `SLE009L` SLE_매출취소
- `SLE010L` SLE_매출정정
- `SLE011L` SLE_온라인결제
- `SLE012L` SLE_모바일결제
- `SLE013L` SLE_승인거절
- `SLE014L` SLE_매출부도
- `SLE015L` SLE_챠지백
- `SLE016L` SLE_공과금납부
- `SLE017L` SLE_교통결제
- `SLE018L` SLE_기프트카드매출
- `SLE019L` SLE_주유소결제
- `SLE020L` SLE_외식결제
- `SLE021M` SLE_가맹점기본
- `SLE022M` SLE_가맹점수수료
- `SLE023M` SLE_가맹점정산
- `SLE024M` SLE_가맹점업종
- `SLE025M` SLE_일별매출집계
- `SLE026M` SLE_월별매출집계
- `SLE027M` SLE_업종별매출
- `SLE028M` SLE_시간대별매출
- `SLE029M` SLE_요일별매출
- `SLE030M` SLE_가맹점일별매출
- `SLE031M` SLE_가맹점월별매출
- `SLE032M` SLE_지역별매출
- `SLE033M` SLE_고객소비패턴
- `SLE034M` SLE_평균결제금액
- `SLE035M` SLE_카드이상거래탐지
- `SLE036M` SLE_가맹점위험
- `SLE037M` SLE_해외매출월
- `SLE038M` SLE_온오프라인비중
- `SLE039M` SLE_신규결제수단
- `SLE040M` SLE_매출예측
- `SLE041M` SLE_혜택적립내역
- `SLE042M` SLE_계절성매출
- `SLE043M` SLE_휴면카드
- `SLE044M` SLE_결제채널분석
- `SLE045M` SLE_연령대별매출
- `SLE046M` SLE_성별매출
- `SLE047M` SLE_일별전체집계
- `SLE048M` SLE_가맹점계약
- `SLE049M` SLE_가맹점단말기
- `SLE050M` SLE_영세우대가맹
- `SLE051M` SLE_무이자할부분담
- `SLE052M` SLE_가맹점프로모션
- `SLE053L` SLE_가맹점마케팅활동
- `SLE054M` SLE_가맹점신용조사
- `SLE055L` SLE_가맹점제재
- `SLE056L` SLE_가맹점민원
- `SLE057L` SLE_정산지연
- `SLE058M` SLE_VAN사연동
- `SLE059M` SLE_가맹점재계약
- `SLE060L` SLE_가맹점해지
- `SLE061H` SLE_가맹점상태이력
- `SLE062L` SLE_이벤트캠페인
- `SLE063L` SLE_캠페인참여
- `SLE064M` SLE_제휴가맹점
- `SLE065L` SLE_가맹점정산지급
- `SLE066M` SLE_가맹점평가
- `SLE067M` SLE_가맹점수수료정책
- `SLE068M` SLE_소상공인지원
- `SLE069L` SLE_선정산
- `SLE070M` SLE_매출채권담보대출


## 9. 외환 (85)

### FXC (40)

- `FXC001L` FXC_환전거래
- `FXC002M` FXC_환율고시
- `FXC003L` FXC_당발송금
- `FXC004L` FXC_타발송금
- `FXC005L` FXC_SWIFT전문
- `FXC006M` FXC_코레스은행
- `FXC007M` FXC_NOSTRO잔액
- `FXC008M` FXC_외환포지션
- `FXC009L` FXC_외환딜링
- `FXC010M` FXC_환전우대
- `FXC011M` FXC_환전지갑
- `FXC012L` FXC_외환수수료
- `FXC013L` FXC_송금취소반환
- `FXC014M` FXC_외화현찰재고
- `FXC015L` FXC_여행자수표
- `FXC016L` FXC_환전예약
- `FXC017L` FXC_외국환거래신고
- `FXC018L` FXC_국세청통보
- `FXC019M` FXC_환율고시차수
- `FXC020M` FXC_외환월말집계
- `FXC021M` FXC_신용장
- `FXC022M` FXC_수출LC
- `FXC023M` FXC_수입LC
- `FXC024M` FXC_내국신용장
- `FXC025L` FXC_수출네고
- `FXC026L` FXC_수입결제
- `FXC027M` FXC_선적서류
- `FXC028L` FXC_추심
- `FXC029L` FXC_수출환어음
- `FXC030L` FXC_유산스
- `FXC031L` FXC_포페이팅
- `FXC032M` FXC_LC조건변경
- `FXC033L` FXC_수입유산스이자
- `FXC034M` FXC_수입보증금
- `FXC035M` FXC_무역보험
- `FXC036L` FXC_무역외환수수료
- `FXC037L` FXC_LC부도
- `FXC038M` FXC_수출통관
- `FXC039M` FXC_수입통관
- `FXC040H` FXC_LC상태이력

### FXR (25)

- `FXR001M` FXR_외화대출기본
- `FXR002M` FXR_외화운영자금
- `FXR003M` FXR_외화시설자금
- `FXR004M` FXR_외화당좌
- `FXR005M` FXR_현지금융
- `FXR006L` FXR_외화대출실행
- `FXR007L` FXR_외화이자산정
- `FXR008L` FXR_외화상환
- `FXR009M` FXR_외화대출담보
- `FXR010M` FXR_외화예금담보
- `FXR011M` FXR_해외부동산대출
- `FXR012M` FXR_해외법인여신
- `FXR013M` FXR_선박금융
- `FXR014M` FXR_항공기금융
- `FXR015M` FXR_해외인프라PF
- `FXR016M` FXR_외화대출한도
- `FXR017M` FXR_대환외화
- `FXR018L` FXR_환차손익
- `FXR019M` FXR_환헤지연계
- `FXR020L` FXR_외화대출연체
- `FXR021M` FXR_외화대출심사
- `FXR022M` FXR_국가익스포저
- `FXR023M` FXR_통화별익스포저
- `FXR024M` FXR_해외차입
- `FXR025H` FXR_외화대출상태이력

### FXD (20)

- `FXD001L` FXD_딜링거래
- `FXD002L` FXD_스팟거래
- `FXD003L` FXD_선물환거래
- `FXD004L` FXD_스왑거래
- `FXD005L` FXD_통화옵션
- `FXD006L` FXD_NDF거래
- `FXD007M` FXD_딜러포지션
- `FXD008M` FXD_딜러
- `FXD009L` FXD_딜확인
- `FXD010L` FXD_딜결제
- `FXD011M` FXD_상대방한도
- `FXD012M` FXD_VaR
- `FXD013L` FXD_딜손익
- `FXD014L` FXD_거래취소정정
- `FXD015M` FXD_옵션구조
- `FXD016M` FXD_이자율스왑
- `FXD017M` FXD_마켓데이터
- `FXD018M` FXD_변동성서페이스
- `FXD019M` FXD_일일포지션한도
- `FXD020M` FXD_일일딜링실적


## 10. 전자금융 (77)

### EBB (15)

- `EBB001M` EBB_인터넷뱅킹가입
- `EBB002L` EBB_로그인이력
- `EBB003L` EBB_이체거래
- `EBB004L` EBB_조회이력
- `EBB005M` EBB_자동이체등록
- `EBB006L` EBB_예약이체
- `EBB007M` EBB_OTP
- `EBB008M` EBB_공동인증서
- `EBB009L` EBB_이상접속
- `EBB010L` EBB_비대면실명확인
- `EBB011M` EBB_이체한도관리
- `EBB012L` EBB_이체지연제도
- `EBB013M` EBB_부가서비스가입
- `EBB014L` EBB_비밀번호변경
- `EBB015M` EBB_장기미사용

### EBM (15)

- `EBM001M` EBM_모바일뱅킹가입
- `EBM002L` EBM_앱접속
- `EBM003M` EBM_생체인증등록
- `EBM004M` EBM_간편PIN
- `EBM005L` EBM_인증시도
- `EBM006M` EBM_앱버전
- `EBM007L` EBM_푸시알림
- `EBM008L` EBM_앱화면이동
- `EBM009M` EBM_기기차단
- `EBM010L` EBM_모바일이체
- `EBM011L` EBM_모바일조회
- `EBM012M` EBM_앱상품가입
- `EBM013M` EBM_활동성
- `EBM014L` EBM_오류로그
- `EBM015M` EBM_마케팅팝업

### EBA (10)

- `EBA001M` EBA_API제휴사
- `EBA002M` EBA_API정의
- `EBA003M` EBA_마이데이터전송
- `EBA004L` EBA_API호출
- `EBA005M` EBA_고객동의관리
- `EBA006L` EBA_데이터전송이력
- `EBA007M` EBA_API한도
- `EBA008M` EBA_간편결제연동
- `EBA009M` EBA_API위반
- `EBA010M` EBA_API실적월별

### EBO (15)

- `EBO001M` EBO_타행계좌연결
- `EBO002L` EBO_오픈뱅킹이체
- `EBO003L` EBO_타행잔액조회
- `EBO004M` EBO_참가사
- `EBO005M` EBO_수수료체계
- `EBO006L` EBO_수수료정산
- `EBO007L` EBO_공동망지급지시
- `EBO008L` EBO_타행거래조회
- `EBO009M` EBO_일한도관리
- `EBO010L` EBO_한도초과거절
- `EBO011M` EBO_공동망접속인증
- `EBO012M` EBO_계좌통합조회동의
- `EBO013L` EBO_예약이체
- `EBO014M` EBO_공동망성능
- `EBO015M` EBO_참가사거래집계

### EBS (22)

- `EBS001L` EBS_이체거래
- `EBS002M` EBS_자동이체등록
- `EBS003L` EBS_자동이체실행
- `EBS004M` EBS_예약이체
- `EBS005M` EBS_대량이체파일
- `EBS006L` EBS_대량이체건
- `EBS007M` EBS_펌뱅킹
- `EBS008L` EBS_CMS수납
- `EBS009L` EBS_CMS지급
- `EBS010L` EBS_급여이체
- `EBS011L` EBS_실명확인
- `EBS012M` EBS_지연이체설정
- `EBS013L` EBS_지연이체보류
- `EBS014M` EBS_수취등록
- `EBS015L` EBS_이체취소
- `EBS016L` EBS_착오송금
- `EBS017M` EBS_수수료정책
- `EBS018M` EBS_수수료면제
- `EBS019L` EBS_OTP사용
- `EBS020M` EBS_이체실적집계
- `EBS021M` EBS_이체이상탐지
- `EBS022M` EBS_전자금융사기피해


## 11. 퇴직연금 (50)

### RPC (20)

- `RPC001M` RPC_가입자기본
- `RPC002M` RPC_사업장
- `RPC003M` RPC_규약
- `RPC004M` RPC_운용관리기관
- `RPC005L` RPC_납입
- `RPC006M` RPC_적립금잔액
- `RPC007L` RPC_수익률
- `RPC008M` RPC_세제혜택
- `RPC009L` RPC_운용지시
- `RPC010M` RPC_상품풀
- `RPC011M` RPC_개별운용현황
- `RPC012L` RPC_중도인출
- `RPC013L` RPC_연금수령
- `RPC014M` RPC_연금수급계획
- `RPC015L` RPC_가입자이동
- `RPC016M` RPC_지급준비금
- `RPC017M` RPC_자산운용수수료
- `RPC018M` RPC_디폴트옵션
- `RPC019L` RPC_연금자산보고서
- `RPC020H` RPC_가입자상태이력

### RPD (20)

- `RPD001M` RPD_DC계정
- `RPD002M` RPD_IRP계정
- `RPD003M` RPD_포트폴리오
- `RPD004M` RPD_TDF보유
- `RPD005L` RPD_리밸런싱
- `RPD006M` RPD_예금보유
- `RPD007M` RPD_펀드보유
- `RPD008L` RPD_매매거래
- `RPD009M` RPD_자동적립
- `RPD010L` RPD_퇴직금전환
- `RPD011M` RPD_위험성향
- `RPD012M` RPD_연금포트추천
- `RPD013L` RPD_운용결과통지
- `RPD014M` RPD_IRP가상계좌
- `RPD015M` RPD_DC상한관리
- `RPD016L` RPD_운용지시이행
- `RPD017M` RPD_가입자교육이수
- `RPD018M` RPD_운용성과평가
- `RPD019M` RPD_IRP이전수수료
- `RPD020M` RPD_IRP월별실적

### RPI (10)

- `RPI001M` RPI_DB가입기업
- `RPI002M` RPI_DB가입자
- `RPI003M` RPI_계리평가
- `RPI004L` RPI_기업부담금
- `RPI005M` RPI_DB운용포트폴리오
- `RPI006L` RPI_퇴직금지급
- `RPI007M` RPI_수급권조회
- `RPI008M` RPI_미적립금
- `RPI009M` RPI_운용지시
- `RPI010H` RPI_DB제도이력


## 12. 신탁·펀드 (45)

### TRS (25)

- `TRS001M` TRS_신탁계약
- `TRS002M` TRS_특정금전신탁
- `TRS003M` TRS_MMT
- `TRS004M` TRS_부동산담보신탁
- `TRS005M` TRS_부동산관리신탁
- `TRS006M` TRS_부동산처분신탁
- `TRS007M` TRS_유언신탁
- `TRS008M` TRS_가업승계
- `TRS009L` TRS_신탁재산거래
- `TRS010M` TRS_신탁보수
- `TRS011M` TRS_수익지급
- `TRS012M` TRS_신탁해지
- `TRS013M` TRS_신탁운용실적
- `TRS014M` TRS_ELT구조
- `TRS015M` TRS_DLS구조
- `TRS016M` TRS_적격투자자
- `TRS017M` TRS_투자권유적합성
- `TRS018L` TRS_설명의무기록
- `TRS019M` TRS_신탁재산가치
- `TRS020M` TRS_금전신탁보고
- `TRS021L` TRS_신탁위약금
- `TRS022M` TRS_분배금
- `TRS023M` TRS_재신탁
- `TRS024M` TRS_신탁업권수수료
- `TRS025M` TRS_부점신탁실적

### FND (20)

- `FND001M` FND_펀드마스터
- `FND002M` FND_자산운용사
- `FND003M` FND_기준가
- `FND004M` FND_고객펀드보유
- `FND005L` FND_펀드매입
- `FND006L` FND_펀드환매
- `FND007L` FND_분배금지급
- `FND008M` FND_판매보수
- `FND009M` FND_펀드수익률
- `FND010M` FND_펀드등급
- `FND011M` FND_적립식펀드
- `FND012M` FND_해외펀드환리스크
- `FND013L` FND_펀드전환
- `FND014M` FND_투자설명서교부
- `FND015M` FND_환매수수료체계
- `FND016M` FND_펀드제한
- `FND017M` FND_펀드판매실적
- `FND018L` FND_불완전판매조사
- `FND019M` FND_미수익계좌
- `FND020M` FND_펀드해지완료


## 13. 투자·파생 (50)

### INV (25)

- `INV001M` INV_투자자산기본
- `INV002M` INV_채권투자
- `INV003M` INV_주식투자
- `INV004M` INV_일평가
- `INV005L` INV_매매거래
- `INV006L` INV_이자수취
- `INV007L` INV_배당수취
- `INV008M` INV_포트폴리오
- `INV009M` INV_포트폴리오집계
- `INV010M` INV_손상평가
- `INV011L` INV_재분류
- `INV012M` INV_사모투자
- `INV013L` INV_캐피탈콜
- `INV014L` INV_분배배당
- `INV015M` INV_대체투자
- `INV016M` INV_한도관리
- `INV017M` INV_발행자집중도
- `INV018M` INV_채권현금흐름
- `INV019M` INV_수익률기여도
- `INV020L` INV_투자의사결정
- `INV021M` INV_벤치마크
- `INV022M` INV_외화투자
- `INV023L` INV_RP거래
- `INV024M` INV_자기주식
- `INV025H` INV_투자상태이력

### DRV (25)

- `DRV001M` DRV_파생기본
- `DRV002M` DRV_금리스왑
- `DRV003M` DRV_통화스왑
- `DRV004M` DRV_선도거래
- `DRV005M` DRV_옵션
- `DRV006M` DRV_CDS
- `DRV007M` DRV_공정가평가
- `DRV008M` DRV_헤지지정
- `DRV009M` DRV_헤지효과성
- `DRV010L` DRV_이자교환
- `DRV011M` DRV_ISDA계약
- `DRV012M` DRV_담보관리
- `DRV013L` DRV_담보이전
- `DRV014M` DRV_청산
- `DRV015L` DRV_조기종결
- `DRV016M` DRV_상대방CVA
- `DRV017M` DRV_고객파생
- `DRV018M` DRV_KIKO
- `DRV019L` DRV_손익실현
- `DRV020M` DRV_파생한도
- `DRV021M` DRV_VaR
- `DRV022M` DRV_스트레스테스트
- `DRV023L` DRV_규제보고
- `DRV024M` DRV_신용위험노출
- `DRV025H` DRV_파생상태이력


## 14. 재무·결산 (90)

### FNA (50)

- `FNA001M` FNA_계정과목
- `FNA002L` FNA_분개원장
- `FNA003M` FNA_계정잔액
- `FNA004M` FNA_부점일계표
- `FNA005L` FNA_역분개
- `FNA006M` FNA_월계정잔액
- `FNA007M` FNA_부점연간
- `FNA008L` FNA_폐쇄분개
- `FNA009M` FNA_기중조정분개
- `FNA010M` FNA_시산표
- `FNA011M` FNA_재무상태표
- `FNA012M` FNA_손익계산서
- `FNA013M` FNA_현금흐름표
- `FNA014M` FNA_자본변동표
- `FNA015M` FNA_관리회계세부
- `FNA016M` FNA_내부이전가격
- `FNA017H` FNA_회계정책변경
- `FNA018M` FNA_이자수익
- `FNA019M` FNA_이자비용
- `FNA020M` FNA_수수료수익
- `FNA021M` FNA_유가증권손익
- `FNA022M` FNA_외환손익
- `FNA023M` FNA_대손비용
- `FNA024M` FNA_인건비
- `FNA025M` FNA_임차감가상각
- `FNA026M` FNA_수수료비용
- `FNA027M` FNA_판관비세분
- `FNA028M` FNA_NIM분석
- `FNA029M` FNA_CIR
- `FNA030M` FNA_ROA_ROE
- `FNA031M` FNA_상품원가
- `FNA032M` FNA_고객수익성
- `FNA033M` FNA_채널수익성
- `FNA034M` FNA_일별손익
- `FNA035M` FNA_결산마감
- `FNA036L` FNA_결산단계
- `FNA037M` FNA_연결대상
- `FNA038M` FNA_연결재무제표
- `FNA039M` FNA_외부감사
- `FNA040L` FNA_감사지적
- `FNA041M` FNA_내부감사
- `FNA042M` FNA_공시
- `FNA043M` FNA_재무제표주석
- `FNA044M` FNA_전표대사
- `FNA045M` FNA_미결전표
- `FNA046M` FNA_가지급금
- `FNA047M` FNA_중요회계추정
- `FNA048M` FNA_전기오류수정
- `FNA049M` FNA_주요재무지표
- `FNA050H` FNA_결산상태이력

### FNB (20)

- `FNB001M` FNB_연간예산
- `FNB002M` FNB_월별배분
- `FNB003M` FNB_예산집행
- `FNB004L` FNB_예산조정
- `FNB005M` FNB_예산수립이력
- `FNB006M` FNB_자본지출
- `FNB007L` FNB_자본지출집행
- `FNB008M` FNB_경비집행
- `FNB009M` FNB_예산실적
- `FNB010M` FNB_부점KPI
- `FNB011M` FNB_예산한도
- `FNB012L` FNB_집행승인
- `FNB013M` FNB_부점목표
- `FNB014M` FNB_추경예산
- `FNB015M` FNB_연간계획
- `FNB016M` FNB_포캐스트
- `FNB017M` FNB_계약경비
- `FNB018M` FNB_부점평가
- `FNB019M` FNB_조달계획
- `FNB020M` FNB_예산성과요약

### FNS (20)

- `FNS001M` FNS_법인세
- `FNS002M` FNS_이연법인세
- `FNS003M` FNS_부가세
- `FNS004L` FNS_원천세납부
- `FNS005L` FNS_이자원천징수
- `FNS006M` FNS_세금우대
- `FNS007M` FNS_비과세
- `FNS008L` FNS_금융소득종합과세
- `FNS009M` FNS_지방세
- `FNS010M` FNS_교육세
- `FNS011L` FNS_세무조정
- `FNS012M` FNS_세액공제
- `FNS013L` FNS_세무조사
- `FNS014M` FNS_외국납부세액
- `FNS015M` FNS_FATCA
- `FNS016L` FNS_증여상속
- `FNS017M` FNS_환급청구
- `FNS018M` FNS_세무공시
- `FNS019M` FNS_영수증증빙
- `FNS020M` FNS_납세이행


## 15. 리스크·규제 (65)

### RSK (30)

- `RSK001M` RSK_신용RWA
- `RSK002M` RSK_시장RWA
- `RSK003M` RSK_운영RWA
- `RSK004M` RSK_자기자본
- `RSK005M` RSK_자본비율
- `RSK006M` RSK_PD모델
- `RSK007M` RSK_LGD모델
- `RSK008M` RSK_EAD산정
- `RSK009M` RSK_유동성LCR
- `RSK010M` RSK_유동성NSFR
- `RSK011M` RSK_유동성갭
- `RSK012M` RSK_금리갭
- `RSK013M` RSK_스트레스시나리오
- `RSK014M` RSK_스트레스결과
- `RSK015M` RSK_ICAAP
- `RSK016M` RSK_집중도위험
- `RSK017L` RSK_한도초과
- `RSK018M` RSK_운영손실
- `RSK019M` RSK_KRI
- `RSK020M` RSK_RAF
- `RSK021M` RSK_자산건전성분류
- `RSK022M` RSK_IFRS9Stage
- `RSK023M` RSK_충당금원장
- `RSK024M` RSK_커버리지비율
- `RSK025M` RSK_예수금변동성
- `RSK026L` RSK_백테스트
- `RSK027M` RSK_모델검증
- `RSK028M` RSK_평판리스크
- `RSK029M` RSK_ESG리스크
- `RSK030H` RSK_리스크지표이력

### RPT (20)

- `RPT001M` RPT_보고서마스터
- `RPT002L` RPT_보고서제출
- `RPT003M` RPT_업무보고서
- `RPT004M` RPT_대손충당금보고
- `RPT005M` RPT_자본적정성
- `RPT006M` RPT_한은금리보고
- `RPT007M` RPT_한은통계
- `RPT008M` RPT_예보보고
- `RPT009M` RPT_중소기업대출
- `RPT010M` RPT_DSR
- `RPT011M` RPT_LTV
- `RPT012M` RPT_외환포지션한도
- `RPT013L` RPT_감독서신
- `RPT014L` RPT_감독검사
- `RPT015L` RPT_제재이력
- `RPT016M` RPT_대주주신용공여
- `RPT017M` RPT_동일인여신
- `RPT018M` RPT_소비자보호
- `RPT019M` RPT_민원보고
- `RPT020M` RPT_통화당국통계

### AML (15)

- `AML001M` AML_위험평가
- `AML002L` AML_의심거래STR
- `AML003L` AML_고액현금거래CTR
- `AML004M` AML_EDD강화고객확인
- `AML005M` AML_제재리스트
- `AML006L` AML_제재스크리닝
- `AML007M` AML_PEP정치적노출
- `AML008M` AML_실소유자
- `AML009M` AML_거래제한
- `AML010M` AML_룰엔진
- `AML011L` AML_룰탐지이력
- `AML012M` AML_고위험국가
- `AML013M` AML_FIU회신
- `AML014M` AML_AML교육
- `AML015H` AML_위험등급이력


## 16. 마케팅·CRM (70)

### MKT (25)

- `MKT001M` MKT_캠페인기본
- `MKT002M` MKT_캠페인대상
- `MKT003L` MKT_접촉내역
- `MKT004L` MKT_전환추적
- `MKT005M` MKT_쿠폰마스터
- `MKT006L` MKT_쿠폰발급
- `MKT007M` MKT_이벤트
- `MKT008L` MKT_이벤트참여
- `MKT009M` MKT_마케팅수신동의
- `MKT010L` MKT_광고집행
- `MKT011M` MKT_캠페인성과
- `MKT012M` MKT_채널별실적
- `MKT013M` MKT_세그먼트
- `MKT014M` MKT_고객세그매칭
- `MKT015M` MKT_프로모션
- `MKT016M` MKT_랜딩페이지
- `MKT017M` MKT_제휴마케팅
- `MKT018L` MKT_추천실적
- `MKT019M` MKT_A_B테스트
- `MKT020M` MKT_캠페인예산
- `MKT021L` MKT_오픈율추적
- `MKT022M` MKT_오프라인캠페인
- `MKT023M` MKT_VIP등급관리
- `MKT024M` MKT_PB상담
- `MKT025H` MKT_캠페인상태이력

### CMG (25)

- `CMG001L` CMG_고객컨택
- `CMG002M` CMG_민원접수
- `CMG003L` CMG_민원처리이력
- `CMG004M` CMG_민원배상
- `CMG005L` CMG_고객의견
- `CMG006M` CMG_NPS조사
- `CMG007M` CMG_고객여정
- `CMG008M` CMG_이탈예측
- `CMG009M` CMG_이탈방지
- `CMG010M` CMG_로열티포인트
- `CMG011L` CMG_포인트거래
- `CMG012M` CMG_LTV
- `CMG013M` CMG_만족도점수
- `CMG014L` CMG_상담녹취
- `CMG015M` CMG_금융소비자보호
- `CMG016L` CMG_디지털여정추적
- `CMG017M` CMG_고객인게이지먼트
- `CMG018M` CMG_고객연락처변경
- `CMG019M` CMG_콜센터배정
- `CMG020M` CMG_챗봇상담
- `CMG021M` CMG_고객청약철회
- `CMG022M` CMG_고객선호
- `CMG023M` CMG_고객페르소나
- `CMG024M` CMG_휴면고객
- `CMG025H` CMG_관계이력

### NBA (20)

- `NBA001M` NBA_추천오퍼
- `NBA002L` NBA_오퍼노출
- `NBA003L` NBA_오퍼클릭
- `NBA004L` NBA_오퍼거절
- `NBA005M` NBA_추천모델
- `NBA006M` NBA_모델성능
- `NBA007M` NBA_교차판매매트릭스
- `NBA008M` NBA_업셀매트릭스
- `NBA009M` NBA_고객세그리드
- `NBA010M` NBA_고객특성피처
- `NBA011M` NBA_상품리스트
- `NBA012M` NBA_피드백
- `NBA013M` NBA_실험트래킹
- `NBA014M` NBA_디스커버리큐
- `NBA015M` NBA_실시간스코어
- `NBA016M` NBA_개인화규칙
- `NBA017L` NBA_추천전환
- `NBA018M` NBA_고객별모델할당
- `NBA019M` NBA_성과월
- `NBA020M` NBA_적합성스크리닝


## 17. 마트 (200)

### MVP (35)

- `MVP001S` MVP_일자부점수신
- `MVP002S` MVP_월자부점수신
- `MVP003S` MVP_일자상품수신
- `MVP004S` MVP_월상품수신
- `MVP005S` MVP_월상품그룹수신
- `MVP006S` MVP_월통화수신
- `MVP007S` MVP_월세그먼트수신
- `MVP008S` MVP_고객수신집계
- `MVP009S` MVP_만기도래
- `MVP010S` MVP_재예치비율
- `MVP011S` MVP_예수평잔
- `MVP012S` MVP_월신규고객수신
- `MVP013S` MVP_수신금액구간
- `MVP014S` MVP_적금납입률
- `MVP015S` MVP_저원가예수금
- `MVP016S` MVP_기간별수신
- `MVP017S` MVP_신탁펀드잔액
- `MVP018S` MVP_외화수신
- `MVP019S` MVP_수신금리분포
- `MVP020S` MVP_성장률분석
- `MVP021S` MVP_VIP수신
- `MVP022S` MVP_급여이체예수금
- `MVP023S` MVP_이탈수신
- `MVP024S` MVP_휴면수신
- `MVP025S` MVP_연령대수신
- `MVP026S` MVP_수신수익성
- `MVP027S` MVP_채널수신
- `MVP028S` MVP_사업자수신
- `MVP029S` MVP_지역수신
- `MVP030S` MVP_연금수신
- `MVP031S` MVP_수신이익기여
- `MVP032S` MVP_전국수신일
- `MVP033S` MVP_핵심예금
- `MVP034S` MVP_예수금대출비율
- `MVP035S` MVP_분기수신

### MVN (35)

- `MVN001S` MVN_일자부점여신
- `MVN002S` MVN_월부점여신
- `MVN003S` MVN_월상품여신
- `MVN004S` MVN_월세그여신
- `MVN005S` MVN_가계기업여신
- `MVN006S` MVN_담보무담보
- `MVN007S` MVN_고객여신집계
- `MVN008S` MVN_연체집계
- `MVN009S` MVN_NPL
- `MVN010S` MVN_대손상각회수
- `MVN011S` MVN_신규취급
- `MVN012S` MVN_여신수익성
- `MVN013S` MVN_여신기간
- `MVN014S` MVN_금리유형
- `MVN015S` MVN_상환방식
- `MVN016S` MVN_외화여신
- `MVN017S` MVN_업종여신
- `MVN018S` MVN_신용등급여신
- `MVN019S` MVN_LTV분포
- `MVN020S` MVN_DSR분포
- `MVN021S` MVN_중도상환
- `MVN022S` MVN_정책여신
- `MVN023S` MVN_무역여신
- `MVN024S` MVN_연체예측실적
- `MVN025S` MVN_여신심사
- `MVN026S` MVN_Stage분포
- `MVN027S` MVN_여신이익기여
- `MVN028S` MVN_연령대여신
- `MVN029S` MVN_지역여신
- `MVN030S` MVN_대기업포트
- `MVN031S` MVN_연체회수
- `MVN032S` MVN_전국여신일
- `MVN033S` MVN_신용공여한도
- `MVN034S` MVN_성장률분석
- `MVN035S` MVN_분기여신

### MVC (20)

- `MVC001S` MVC_일자카드매출
- `MVC002S` MVC_월카드매출
- `MVC003S` MVC_월카드회원
- `MVC004S` MVC_할부매출
- `MVC005S` MVC_현금서비스
- `MVC006S` MVC_업종매출
- `MVC007S` MVC_가맹점매출
- `MVC008S` MVC_해외매출
- `MVC009S` MVC_결제집계
- `MVC010S` MVC_카드연체
- `MVC011S` MVC_고객카드집계
- `MVC012S` MVC_카드수익성
- `MVC013S` MVC_체크카드
- `MVC014S` MVC_카드발급효율
- `MVC015S` MVC_리볼빙
- `MVC016S` MVC_한도사용률
- `MVC017S` MVC_포인트실적
- `MVC018S` MVC_이탈카드
- `MVC019S` MVC_카드전국일
- `MVC020S` MVC_분기카드

### MVF (15)

- `MVF001S` MVF_월환전실적
- `MVF002S` MVF_월송금실적
- `MVF003S` MVF_월무역실적
- `MVF004S` MVF_외화예금
- `MVF005S` MVF_외화대출
- `MVF006S` MVF_FX딜링
- `MVF007S` MVF_지역별송금
- `MVF008S` MVF_외환고객집계
- `MVF009S` MVF_환율영향
- `MVF010S` MVF_외환수수료
- `MVF011S` MVF_외환유학
- `MVF012S` MVF_외환딜링마진
- `MVF013S` MVF_외화유동성
- `MVF014S` MVF_외환전국일
- `MVF015S` MVF_분기외환

### MVB (25)

- `MVB001S` MVB_부점일실적
- `MVB002S` MVB_부점월실적
- `MVB003S` MVB_부점분기실적
- `MVB004S` MVB_부점NIM
- `MVB005S` MVB_부점KPI
- `MVB006S` MVB_부점수익성
- `MVB007S` MVB_인력생산성
- `MVB008S` MVB_부점고객
- `MVB009S` MVB_부점창구
- `MVB010S` MVB_부점여신건전성
- `MVB011S` MVB_부점상품구성
- `MVB012S` MVB_부점신규유치
- `MVB013S` MVB_부점해지
- `MVB014S` MVB_지역부점
- `MVB015S` MVB_본부부점
- `MVB016S` MVB_부점채널
- `MVB017S` MVB_부점예대율
- `MVB018S` MVB_부점평가
- `MVB019S` MVB_부점연환산
- `MVB020S` MVB_부점전국합계
- `MVB021S` MVB_SME영업점
- `MVB022S` MVB_PB센터
- `MVB023S` MVB_디지털부점
- `MVB024S` MVB_부점주요지표
- `MVB025S` MVB_부점추이스냅샷

### MRC (30)

- `MRC001S` MRC_고객수월
- `MRC002S` MRC_활성고객
- `MRC003S` MRC_세그먼트분포
- `MRC004S` MRC_신규유치월
- `MRC005S` MRC_이탈월
- `MRC006S` MRC_연령대분포
- `MRC007S` MRC_지역분포
- `MRC008S` MRC_직업분포
- `MRC009S` MRC_수익성분포
- `MRC010S` MRC_RFM분석
- `MRC011S` MRC_LTV분포
- `MRC012S` MRC_VIP고객수
- `MRC013S` MRC_휴면고객
- `MRC014S` MRC_생존분포
- `MRC015S` MRC_코호트분석
- `MRC016S` MRC_교차거래
- `MRC017S` MRC_디지털전환율
- `MRC018S` MRC_유치채널성과
- `MRC019S` MRC_크로스셀성과
- `MRC020S` MRC_이탈예측적중
- `MRC021S` MRC_유형별수익
- `MRC022S` MRC_NPS분포
- `MRC023S` MRC_상담분포
- `MRC024S` MRC_민원분포
- `MRC025S` MRC_복수상품보유
- `MRC026S` MRC_활성도변동
- `MRC027S` MRC_자녀고객
- `MRC028S` MRC_가족관계
- `MRC029S` MRC_소득수준별
- `MRC030S` MRC_주거래은행화율

### MRP (15)

- `MRP001S` MRP_수신잔액
- `MRP002S` MRP_여신잔액
- `MRP003S` MRP_수익성
- `MRP004S` MRP_가입해지
- `MRP005S` MRP_신규상품
- `MRP006S` MRP_단종상품
- `MRP007S` MRP_상품전환
- `MRP008S` MRP_라이프사이클
- `MRP009S` MRP_만족도
- `MRP010S` MRP_KPI
- `MRP011S` MRP_포트폴리오
- `MRP012S` MRP_원가이익
- `MRP013S` MRP_금리경쟁력
- `MRP014S` MRP_AB테스트
- `MRP015S` MRP_신청전환

### MRO (15)

- `MRO001S` MRO_인력생산성
- `MRO002S` MRO_영업비용
- `MRO003S` MRO_수익성
- `MRO004S` MRO_상권
- `MRO005S` MRO_경쟁현황
- `MRO006S` MRO_규모분석
- `MRO007S` MRO_성과등급
- `MRO008S` MRO_혁신지수
- `MRO009S` MRO_운영효율
- `MRO010S` MRO_디지털부점
- `MRO011S` MRO_만족도
- `MRO012S` MRO_민원지수
- `MRO013S` MRO_임원평가
- `MRO014S` MRO_KPI종합
- `MRO015S` MRO_통폐합후보

### MRR (10)

- `MRR001S` MRR_월차경영보고
- `MRR002S` MRR_분기규제지표
- `MRR003S` MRR_임원경영성과
- `MRR004S` MRR_이사회안건
- `MRR005S` MRR_경영계획대비
- `MRR006S` MRR_중점관리지표
- `MRR007S` MRR_업권비교
- `MRR008S` MRR_주주총회지표
- `MRR009S` MRR_규제한도관리
- `MRR010S` MRR_ESG지표


---

## 주제영역별 합계

| # | 주제영역 | 계획 | 실제 |
|---|---|---|---|
| 1 | 공통·조직·코드·시스템 (CMI/CMO/CMS) | 50 | 50 |
| 2 | 고객·CIF·신용평가 (CSC/CSI/CSK) | 80 | 81 |
| 3 | 상품·약관·금리 (PFP/PFR/PFC) | 55 | 55 |
| 4 | 수신 (DPG/DPF/DPD/DPB/DPN/DPY) | 145 | 146 |
| 5 | 여신 (LNB/LNH/LNJ/LNC/LNK/LNW/LNO) | 210 | 211 |
| 6 | 담보·보증 (LNM/LNG) | 45 | 46 |
| 7 | 카드 회원 (CLN) | 50 | 50 |
| 8 | 카드 매출·정산 (SLE) | 70 | 70 |
| 9 | 외환 (FXC/FXR/FXD) | 85 | 85 |
| 10 | 전자금융 (EBB/EBM/EBA/EBO/EBS) | 75 | 77 |
| 11 | 퇴직연금 (RPC/RPD/RPI) | 50 | 50 |
| 12 | 신탁·펀드 (TRS/FND) | 45 | 45 |
| 13 | 투자·파생 (INV/DRV) | 50 | 50 |
| 14 | 재무·결산 (FNA/FNB/FNS) | 90 | 90 |
| 15 | 리스크·규제 (RSK/RPT/AML) | 65 | 65 |
| 16 | 마케팅·CRM (MKT/CMG/NBA) | 70 | 70 |
| 17 | 마트 (MVP/MVN/MVC/MVF/MVB/MRC/MRP/MRO/MRR) | 200 | 200 |
| | **합계** | **1,435** | **1,441** |

Phase B 신설 및 유형 재분류로 계획 1,435 → 실제 **1,441** (+6)