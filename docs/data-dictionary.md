# 전처리 자료 사전

`data/source_manifest.json`은 원본 파일명, byte 크기, SHA-256, CP949 인코딩, 지점별 행수, 기간, 계절 파일과의 일치 검사를 기록한다. 원본 경로와 관측 행은 넣지 않는다.

`outputs/preprocessed/incheon_2025_hourly.csv`의 한 행은 인천 ASOS 112의 표기 시각(KST) 하나다. `time_kst`는 시간대 없는 문자열이지만 KST로 정의한다. 기본 열은 `temp_c`, `rain_mm`, `wind_m_s`, `rh_pct`, `vapor_hpa`, `pressure_hpa`, `solar_mj_m2`, `cloud_tenths`, `snow_cm`, `new_snow_cm`, `ground_30cm_c`다. 각 열에는 `_raw` 원문 문자열, `_qc` 원문 QC(원자료에 없는 변수는 `not_provided`), `_status` 사용·결측 사유가 붙는다. 변환 열 `temp_k`, `rh_fraction`, `pressure_pa`, `vapor_pa`, `cloud_fraction`, `solar_w_m2`는 SI 계산용이다. 결측은 빈 필드로 유지한다. `solar_mj_m2_status=assumed_dark_interval_zero`만 원문 공란을 물리적 0으로 가정한 경우다. QC9와 값의 충돌은 사용값을 비우되 `_raw`와 `_qc`에서 확인할 수 있다.

`outputs/preprocessed/day_catalog.csv`의 한 행은 해당 날짜 06:00 초과부터 다음 날 06:00 포함까지의 누적 24개 구간이다. `interval_rows`는 원자료 구간 수, `temp_rows`와 `solar_rows`는 사용 가능 수다. `solar_mj_m2_sum_observed`는 사용 가능 부분의 합이며 결측이 있으면 완전한 일사합이 아니다. `rain_positive_mm_lower_bound`는 양의 기록만 더한 하한이다. `rain_unknown_hours`와 `rain_qc9_hours`는 무강수 판단 불확실성을 보여준다. `rain_status=unresolved_missing`은 강수 미확정이며 0 강수를 뜻하지 않는다. `snow_status=unresolved_no_positive_record`는 적설 관측 양의 값이 없을 뿐 무적설을 확정하지 않는다. `full_meteorology`는 현재는 24개 기온·일사 입력만 검사하는 임시 플래그로, 모든 기상변수와 예열이 준비되었다는 뜻이 아니다.
