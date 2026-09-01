# Power Automate 실무 가이드

Microsoft Power Automate(구 Microsoft Flow)를 실무에서 쓰기 위한 한국어 가이드입니다.
UI 클릭 순서를 나열하는 대신, **왜 그렇게 만들어야 하는지**와 **자주 밟는 지뢰**에 초점을 맞췄습니다.

## 목차

| 장 | 문서 | 내용 |
| --- | --- | --- |
| 1 | [기초](01-basics.md) | 플로우 종류, 구성 요소, 라이선스, 환경 |
| 2 | [트리거](02-triggers.md) | 트리거 유형, 트리거 조건, 동시성, 분할 개수 |
| 3 | [액션과 데이터 처리](03-actions-and-data.md) | 커넥터, 데이터 작업, 변수, OData 쿼리 |
| 4 | [표현식](04-expressions.md) | 표현식 문법과 함수 레퍼런스 |
| 5 | [제어 흐름](05-control-flow.md) | 조건, 스위치, 반복, 범위, 병렬 |
| 6 | [오류 처리](06-error-handling.md) | 실행 구성, try/catch, 재시도 정책 |
| 7 | [성능과 제한](07-performance-limits.md) | 최적화 기법, 서비스 제한 |
| 8 | [솔루션과 배포(ALM)](08-alm-deployment.md) | 솔루션, 환경 변수, 연결 참조, 파이프라인 |
| 9 | [실전 레시피](09-recipes.md) | 바로 쓰는 패턴 모음 |
| 10 | [문제 해결](10-troubleshooting.md) | 자주 나오는 오류와 원인 |
| 11 | [VBA 연동](11-vba-integration.md) | Excel VBA에서 플로우 호출, PPT 자동 보고 |

## 학습 경로

**처음이라면** 1장 → 2장 → 5장 → 9장 순서로 읽고 간단한 플로우를 하나 만들어 보세요.

**이미 플로우를 만들어 봤다면** 4장(표현식)과 6장(오류 처리)이 가장 효과가 큽니다.
실무 플로우가 깨지는 원인은 대부분 표현식의 `null` 처리와 오류 경로 누락입니다.

**운영에 올려야 한다면** 7장과 8장을 먼저 보세요. 기본 환경에 직접 만든 플로우는
다른 환경으로 옮길 수 없습니다(8장 참고).

## 용어 정리

| 한국어 UI | 영어 UI | 설명 |
| --- | --- | --- |
| 클라우드 플로우 | Cloud flow | 클라우드에서 실행되는 워크플로 |
| 데스크톱 플로우 | Desktop flow | PC에서 실행되는 RPA 자동화 |
| 트리거 | Trigger | 플로우를 시작시키는 이벤트 |
| 액션 | Action | 트리거 이후 수행하는 단계 |
| 커넥터 | Connector | 외부 서비스와 통신하는 어댑터 |
| 연결 | Connection | 커넥터에 대한 인증 정보 |
| 각각에 적용 | Apply to each | 배열 반복 |
| 작성 | Compose | 값을 계산해 두는 액션 |
| JSON 구문 분석 | Parse JSON | JSON에 스키마를 입혀 동적 콘텐츠로 노출 |
| 배열 필터링 | Filter array | 배열에서 조건에 맞는 항목만 추출 |
| 범위 | Scope | 여러 액션을 하나로 묶는 컨테이너 |
| 실행 구성 | Configure run after | 이전 단계 결과에 따른 실행 조건 |
| 솔루션 | Solution | 환경 간 이동 단위(패키지) |

> UI 언어를 영어로 바꾸려면 Power Automate 우측 상단 설정 → 언어 및 시간에서 변경합니다.
> 온라인 자료 대부분이 영어 액션 이름을 쓰기 때문에, 검색이 잦다면 영어 UI가 편합니다.

## 공식 문서

- Power Automate 문서: <https://learn.microsoft.com/ko-kr/power-automate/>
- 표현식 함수 레퍼런스: <https://learn.microsoft.com/ko-kr/azure/logic-apps/workflow-definition-language-functions-reference>
- 제한 및 구성: <https://learn.microsoft.com/ko-kr/power-automate/limits-and-config>
- 커넥터 레퍼런스: <https://learn.microsoft.com/ko-kr/connectors/>

> 이 문서의 수치(제한값, 라이선스별 요청 한도, 제품 이름)는 자주 바뀝니다.
> 계약이나 아키텍처 결정에 쓰기 전에 위 공식 문서에서 최신 값을 확인하세요.
