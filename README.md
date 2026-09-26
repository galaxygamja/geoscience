# Geoscience 공동탐구

인천 지역 포장 표면의 반사율과 수분 공급이 열환경에 미치는 영향을 계절별 기상 조건에서 시뮬레이션하는 프로젝트입니다.

**최신 검토안(2026-09-26):** [콘크리트 2층 모델: 재료 선택과 물 이동](docs/concrete-model-draft.md), [수식 PDF](output/pdf/concrete-model-draft.pdf), [편집용 LaTeX](docs/concrete-model-draft.tex). 보수재 충전 콘크리트 WCC와 재생 쇄석 지지층을 후보로 비교하고, 네 상태 열·물수지와 양방향 Darcy형 물 이동식을 설명합니다. 물성은 문헌값·환산값·대리값·미확정을 구별했으며, 시뮬레이션 입력 전체를 확정한 단계는 아닙니다.

기존 계획의 7-8월 분석에 1-2월을 추가하고, 장비 부족으로 수행하기 어려운 시편 실험은 수치 검증, 민감도 분석, 불확실성 분석으로 대체합니다.

구현 순서, 물리 모델과 비교 조건은 [시뮬레이션 실행 계획](docs/simulation-plan.md)에 정리되어 있습니다. **최신 범위는 위 0–5 cm 다공성·보수성 콘크리트와 아래 5–30 cm 지지층**이며, 상세한 [층의 깊이·물 이동 검토안](docs/layer-water-review.md)과 [콘크리트 물성 근거 1차 점검](docs/concrete-parameter-review.md)을 우선합니다. 일정 하부 배수를 포함하고 동결 계산은 제외하며, 겨울은 건조 또는 비동결 적용 범위 안에서 평가합니다. 추가 기상자료 없이 개발을 시작하며, 강수·적설 판정이 불확실한 날은 적용 범위를 제한합니다. 수치 검증을 실제 포장 실험 검증과 동일하게 취급하지 않습니다.

날짜 선정, 06시 급수 절차, 3×3 요인 비교, 평가 지표와 심화 분석은 [수치실험 설계](docs/experiment-design.md)에 자세히 기술했습니다.

현재 입력 자료는 실제 기상 관측값을 포함하며, 인천 112의 기본 시뮬레이션에 사용할 수 있습니다. 계절 자료 점검은 [`docs/initial-review.md`](docs/initial-review.md), 2025년 연간 시간자료 점검은 [`docs/annual-data-review.md`](docs/annual-data-review.md)에 정리되어 있습니다.

1·2단계 진행 기록: [전처리 규칙과 현재 판정](docs/preprocessing-rules.md), [선행연구·방정식 감사표](docs/model-literature.md), [원본 해시 목록](data/source_manifest.json). 전처리 코드는 `src/geoscience/preprocess.py`에 있으며, 생성되는 시간별 자료와 날짜 후보표는 로컬 `outputs/preprocessed/`에만 둡니다. 강수·적설과 황혼 시간 일사, 일부 물성·경계조건이 아직 미확정이므로 시뮬레이션 결과를 산출한 단계는 아닙니다.

2026-09-25: [핵심 방정식과 논문별 근거](docs/core-equations.md)를 사용자 검토용으로 작성했습니다. 수식이 깨지면 [조판된 PDF](output/pdf/core-equations.pdf)를 읽고, 수정 가능한 [LaTeX 원본](docs/core-equations.tex)을 사용할 수 있습니다. 이 문서와 PDF는 **이전 세 상태·한 물 저장소 방정식안**으로, 최신 콘크리트+지지층 네 상태 모델의 완성본이 아닙니다. 보존식의 출처는 참고할 수 있지만 세라믹 재료 가정과 물성 근거는 새 [물성 감사표](docs/concrete-parameter-review.md)를 따릅니다. [열수지 유도 부록](docs/equation-derivation.md)에서 물의 열용량·증발 잠열·급수 에너지의 결합을 확인할 수 있습니다. 물성 수치는 아직 확정하지 않았습니다.

PDF 재생성: `node scripts/render_core_equations.mjs concrete-model-draft` 다음 `node scripts/print_equations.mjs concrete-model-draft`. 로컬 KaTeX와 Chromium을 사용한 PDF 조판이며, `.tex`는 별도 편집용으로 내보냅니다. XeLaTeX 컴파일 검증과 PDF 렌더링 검증은 서로 다릅니다.
