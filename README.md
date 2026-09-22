# Geoscience 공동탐구

인천 지역 포장 표면의 반사율과 수분 공급이 열환경에 미치는 영향을 계절별 기상 조건에서 시뮬레이션하는 프로젝트입니다.

기존 계획의 7-8월 분석에 1-2월을 추가하고, 장비 부족으로 수행하기 어려운 시편 실험은 수치 검증, 민감도 분석, 불확실성 분석으로 대체합니다.

구현 순서, 물리 모델, 9개 비교 조건, 겨울 동결 처리와 검증 기준은 [시뮬레이션 실행 계획](docs/simulation-plan.md)에 정리되어 있습니다. 추가 기상자료 없이 개발을 시작하며, 강수·적설 판정이 불확실한 날은 적용 범위를 제한합니다. 수치 검증을 실제 포장 실험 검증과 동일하게 취급하지 않습니다.

날짜 선정, 06시 급수 절차, 3×3 요인 비교, 평가 지표와 심화 분석은 [수치실험 설계](docs/experiment-design.md)에 자세히 기술했습니다.

현재 입력 자료는 실제 기상 관측값을 포함하며, 인천 112의 기본 시뮬레이션에 사용할 수 있습니다. 계절 자료 점검은 [`docs/initial-review.md`](docs/initial-review.md), 2025년 연간 시간자료 점검은 [`docs/annual-data-review.md`](docs/annual-data-review.md)에 정리되어 있습니다.

1·2단계 진행 기록: [전처리 규칙과 현재 판정](docs/preprocessing-rules.md), [선행연구·방정식 감사표](docs/model-literature.md), [원본 해시 목록](data/source_manifest.json). 전처리 코드는 `src/geoscience/preprocess.py`에 있으며, 생성되는 시간별 자료와 날짜 후보표는 로컬 `outputs/preprocessed/`에만 둡니다. 강수·적설과 황혼 시간 일사, 일부 물성·경계조건이 아직 미확정이므로 시뮬레이션 결과를 산출한 단계는 아닙니다.
