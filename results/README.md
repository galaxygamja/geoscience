# 승인 후 최종 연구 결과

한국어 해석은 [최종 보고서](../docs/final-results.md)를 먼저 읽는다. 이것은 문헌 대리 WCC/RCA의 수치 비교이며 현장 실측 온도/제품 검증이 아니다.

- `raw/*.json.gz`: 수렴, 초기화, 기본 비교, 30개 민감도, 21단계 살수 결과. 시간별 4상태와 물·열 장부, 날짜별 제외 사유, 설정 및 출처 해시를 포함한다. `gzip`으로 읽는 JSON이다.
- `gates.json`: 시간 간격 수렴과 초기상태 진단. `spinup_1`은 초기상태 비의존성 기준 미달이며 결과는 72시간 초기화에 조건부다.
- `baseline_summary.csv`: 기준의 전체 적격 날짜와 민감도 공통 날짜를 **서로 다른 scope**로 집계했다. 두 scope의 행을 합산하지 않는다.
- `dose_response.csv`, `water_choices.csv`: 같은 반사율 무살수 대비 효과와 95%/90% 격자 내 최소량. 기준 전체 날짜/민감도 공통 날짜를 분리한다.
- `sensitivity_summary.csv`, `sensitivity_ranges.csv`: 전체 시나리오 공통 날짜의 OAT 결과. 범위는 신뢰구간이 아니다. 저장량 시나리오는 절대 공급량도 달라진다.
- `cohort_counts.csv`, `summary.json`: 0일 집단 포함 표본 수, 실제 날짜, 핵심 요약, 원자료 해시.
- `verification.json`: 보존장부·시간별 합·처리 그리드·날짜 회계·단계 사이 재사용 결과의 재계산 점검.
- `figures/`: CSV로 재생성한 PNG/SVG 연구 그림. 그림의 표면온도는 시간 끝점 최고값의 날짜 평균이다.
- `provenance/runner-before-gate.py`: 수렴/초기화 실행 당시 runner 원문. 이후 runner에는 수렴 통과 후 본 계산을 허용하는 검사가 추가됐다. 물리 적분 소스는 모든 단계에서 동일하다.

## 표의 의미

`scope=baseline_all`은 기준 56일(여름55, 겨울1)의 해당 계절/강수군이다. `scope=sensitivity_common`은 모든 민감도에 공통인 여름50일만 사용한다. `cohort`는 평가24시간의 기록 강수로 나눈다. 기록상0일에도 이전 강수의 저장수가 남을 수 있다. 기상 누락·눈·동결 제외로 전체 여름/겨울 대표 표본이 아니다.

`mean_daily_max_surface_c`는 각 날짜 시간별 최고값의 평균이며 순간 동시 온도차와 다르다. `mean_watering_effect_k_h`는 같은 반사율의 무살수 `∫max(Ts−Ta,0)dt`에서 살수 값을 뺀 날짜별 차이의 평균이다. `mean_interaction_k_h > 0`은 살수 효과가 α=0.10보다 작음을 뜻하며, 상승적 결합 효과를 뜻하지 않는다.

공급량 `L/m²`는 모델의 ρ=1000 kg/m³에서 `kg/m²`, 수심 `mm`와 수치가 같다. 투입량 전체가 증발하거나 저장되었다는 뜻이 아니다. 반사 단파는 위로 반사된 면적당 에너지이며 보행자가 흡수한 복사나 체감온도가 아니다.

## 결과만 재생성 — 적분 없음

저장된 파일을 사용하므로 원본 Downloads CSV나 네트워크가 필요 없다.

```sh
python3 scripts/check_final_gates.py
python3 scripts/analyze_final_study.py
python3 scripts/verify_final_results.py
python3 -m unittest discover -s tests
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-figures.txt
.venv/bin/python scripts/plot_final_study.py
```

처음 네 명령은 Python 3.10+ 표준 라이브러리만 필요하다. 그림만 matplotlib가 필요하며 설치는 네트워크를 사용한다. 과학적 입력은 설치로 바뀌지 않는다.

## 전체 적분 재현 — 명시적 승인 플래그 필요

기존 완료 산출물을 덮어쓰지 않으므로 새로운 체크아웃에서 `results/raw`의 출력 이름을 새 이름으로 정하거나 백업 후 아래 경로를 비워 실행한다. 집계/점검 스크립트는 기본 파일명을 읽는다. 캐시는 `outputs/final-cache`이며 없으면 계산하고 있으면 완전한 입력+소스 해시가 같은 작업만 재사용한다.

```sh
python3 scripts/run_final_study.py --stage convergence --approve-final-comparison --output results/raw/convergence.json.gz
python3 scripts/run_final_study.py --stage initialization --approve-final-comparison --output results/raw/initialization.json.gz
python3 scripts/check_final_gates.py
python3 scripts/run_final_study.py --stage coarse --approve-final-comparison --output results/raw/coarse.json.gz
python3 scripts/run_final_study.py --stage sensitivity --approve-final-comparison --output results/raw/sensitivity.json.gz
python3 scripts/run_final_study.py --stage refinement --approve-final-comparison --output results/raw/refinement.json.gz
python3 scripts/analyze_final_study.py
python3 scripts/verify_final_results.py
```

입력 구성의 `final_comparison_authorized=false`는 승인 전 준비 기록이다. 이번 실행은 사용자 후속 명시 승인과 CLI 플래그를 `raw`의 authorization 필드 및 [실행 계획](../docs/final-execution-plan.md)에 별도 기록했다. 임의로 실행 잠금을 삭제하지 않는다. 종료 시각 등 영수증 메타데이터는 재실행 때 달라질 수 있다.
