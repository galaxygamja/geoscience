# 최종 비교 실행 전 점검표

2026-09-26. **사용자의 RCA 대리 지지층 선택까지 반영하여 실행 전 준비를 완료했다. 최종 시뮬레이션 결과는 없다.**

아래 25cm는 `RCA_literature_proxy`로 확정했다. 연구 범위 결정의 미응답 항목은 없으며, 최종 비교 실행에 대한 추가 승인만 별도로 남는다.

## 준비물

- [이전 대화 결정 인수](conversation-handoff-2026-09-26.md): 같은 프로젝트 task 전체 로컬 기록, 사용자40메시지·완료27답변의 결정/수정 반영.
- [입력 근거표](preflight-input-evidence.md), [기계판독 설정](../config/research-inputs.json), [문헌 확보 해시](../data/reference-manifest.json).
- [기상 묶음](../data/preflight/incheon2025-weather.json.gz): 인천112의8,736시간,原觀測/QC/처리 사유/일강수 보조182일 포함. 제3자가 Downloads 경로 없이 Git에서 재사용 가능.
- [후보·제외 목록과 해시](../data/preflight/manifest.json), [환산 재료](../data/preflight/baseline-material.json).
- [실험 조건](experiment-design.md): 3×3, 기준 포함30시나리오,5%급수격자,계절/강수군 공통 날짜. 높은 반사율의 자동 최적화 없음.

## 정적 점검 결과

| 월 | 후보 | 24시간 기상 입력 가능 | 72시간 사전 구간+첫 급수 물온도까지 준비 가능 |
|---|---:|---:|---:|
| 1 | 31 | 18 | 2 |
| 2 | 28 | 21 | 3 |
| 7 | 31 | 31 | 29 |
| 8 | 31 | 29 | 26 |
| 합계 | 121 | 99 | 60 |

수분을 포함한72시간 사전 적분은 아직 실행하지 않았다. 겨울5일도 추후 젖은 층의 동결 조건에 걸릴 수 있다. 영하 기온이 없다는 것만으로 복사냉각에 의한 표면동결을 배제하지 않는다. 눈이 보고되지 않은 시각도 잔설이0이라는 실측 증거가 아니다.

원본26,208행 중 인천8,736행을 선택한다. 연간 인천24시간이 원본에서 빠져 있으며 원본을 꾸며 채우지 않는다. 1월초 사전 구간 부족은 제외 사유이고12월말은 비교기간 밖이다. 변환 전후 해시, 중복 시간, 양의 열용량/저장상한, β와 반사율 범위, 급수 총량, 기상 forcing의 단위/물리 범위 및 입력 잠금을 검사한다.

## 지금 실행할 수 있는 준비 명령

```sh
python3 scripts/prepare_research.py --annual '/실제/2025년_시간단위_전체데이터.csv'
python3 -m unittest discover -s tests -v
```

첫 명령은 `data/preflight/`를 재생성하며 **시간 적분 함수를 호출하지 않는다**. 원본 SHA가 기존 검토본과 다르면 중단한다. 30시나리오의 날짜/물성/기상 적합성을 정적으로 확인한다. 생성물의 `research_integrations_executed=0`은 이 생성기의 실행 범위를 뜻한다.

기존 테스트는 합성 물성·합성 기상 또는 단위 변환 검증이다. 실제 인천+연구 물성의 최종 온도, 냉각 또는 최적량 비교는 테스트에서 실행하지 않는다.

## 추가 승인 이후에만 허용되는 명령

아래는 실행하지 않은 사용법이다. 먼저 사용자가 최종 연구 시뮬레이션 실행을 별도로 승인해야 한다. `--approve-final-comparison`을 생략하면 기상/모델 적분 전 오류로 종료한다. 스키마의 연구결정이 미확정이면 플래그가 있어도 막힌다.

```sh
python3 -m src.geoscience.comparison --stage convergence --approve-final-comparison --output outputs/final/convergence.json
# 수렴·초기조건 점검 결과를 검토한 뒤에만 다음 단계 실행
python3 -m src.geoscience.comparison --stage coarse --approve-final-comparison --output outputs/final/coarse.json
python3 -m src.geoscience.comparison --stage sensitivity --approve-final-comparison --output outputs/final/sensitivity.json
python3 -m src.geoscience.comparison --stage refinement --approve-final-comparison --output outputs/final/refinement.json
```

**자동화 범위:** 물·열 장부 허용오차는 실행기가 검사한다. 20/10/5초의 온도 차이와 초기조건/예열기간 영향은 출력된 같은 날짜·조건의 시간별 상태를 사용해 검토해야 한다. `temperature_convergence_tolerance_k=0.05`는 사전 기준이며 현재 CLI가 자동으로 통과 판정을 내리는 기능은 없다. 이번 정적 점검을 실제입력 수렴 통과라고 읽지 않는다.

CLI는 설정·코드·기상묶음 해시가 준비본과 다른 경우 재생성을 요구하고 기존 결과 파일을 덮어쓰지 않는다. 승인 플래그는 운영상의 명시적 정지선이며 악의적 코드 수정까지 막는 보안 장치가 아니다. 직접 `simulate.run`도 `research_run=true` 입력은 승인 인자가 없으면 거부한다.

## 이번에 하지 않은 일과 해석 제한

실제 연구 물성으로 사전 적분, 실제입력 수렴, 온도/냉각 비교, 최적량 산출은 **0회**. 현장 보정·실측 정확도 검증·보행자 체감온도 산출도 없다. 확보 불가 계수는 입력에서 대리/가정으로 해소했고, 실측으로 포장하지 않았다. 최종 유효날짜와 효과 부호는 실행 후에만 알 수 있다.

최종 시험·독립 검토·GitHub 반영 증거는 [작업 기록](preflight-worklog.md)의 마지막 항목에 기록한다.
