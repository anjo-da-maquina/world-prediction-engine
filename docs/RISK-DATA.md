# 危機シナリオの入力仕様（v0.4）

入力はJSON。`python -m wpe.risk import`で読み込みます。
トップレベルは`evidence / metrics / observations / scenarios / judicial_records`の配列のみ。不要な配列は省略可能。
例：`examples/risk-demo.json`。各レコードの項目は例と完全一致させます。未定義の項目はエラー。
エラーなら入力全体をロールバックします。同一ID・内容の再登録は何も変更しません。

## 原文の証拠：evidence

`source_url`は認証情報なしのHTTPS。原文は`source_text`、引用は`quote`。
`start/end`はPython文字位置、区間は[start,end)。原文と完全一致しない引用は拒否します。
`captured_at`は取得日時、`published_at`は公表日時。不明ならnull。
`claim_status`は`report / allegation / court_finding / statistic / hypothesis / correction`。
`independence_group`は同じ配信や資料の転載をまとめる識別子です。独立性の自動認定は行いません。
これらの分類は登録者による指定です。引用一致は主張の真実性を保証しません。

## 指標：metrics

`definition / unit / population / source_id`は比較条件の説明です。
`measure`は次のいずれかです。

- reported_incidents、victim_survey、suspect_registration
- arrests、prosecutions、convictions、reporting_rate
- service_capacity、context、news_mentions

`mode`はdemo/live。同一シナリオで両方を混ぜられません。
定義変更は新しいmetric IDとして登録し、別の指標として扱います。

## 観測：observations

指標ID、地域、観測期間、判明日時、値、分母、根拠を指定します。
`period_start <= period_end <= known_at`が必要。
`denominator`はnullまたは正の数。reporting_rateは0〜1、context以外の値は非負です。
同一指標・地域・period_end・known_atの二重登録は禁止。改訂は新しいknown_atとIDを使います。
数値の時系列表示では、情報基準時刻までに判明した最新の版を期間ごとに選びます。
同じ指標の観測期間の長さが違う場合や期間が重なる場合は、地域集計を拒否します。
人口当たりに換算できるのは件数指標のみ。分母の正しさは別に確認してください。

## シナリオ：scenarios

同じIDでrevisionを1,2,3…と増やせます。既存版は変更不可。created_atは版ごとに単調増加。
`probability`はnullのみ。`probability_reason`に確率を算出できない理由を記録。
`legal_class`はcrime/harmful_practice/rights_violation/service_failure。
severityはlow/moderate/high/critical、evidence_strengthはlimited/moderate/strong。いずれも登録者の評価。
地域、被害種類、期限、結果判定条件、必要条件、先例、警戒指標、根拠、対策、解除条件、限界は必須。

指標条件はmetric_id/op/threshold/max_age_hours。
opはgt/ge/lt/le/eq。max_age_hoursは観測期間末尾からの許容経過時間です。
警戒指標はAND、反証指標はOR。必要な観測が欠けている・古い場合はunknown。
判定優先順：expired → refuted → unknown → watch → not_triggered。
期限切れは結果確定ではありません。期限後にresolveで手動判断を残します。

「必要条件」の文章や先例から自動で判断するLLMはありません。実際の判定は登録された数値指標だけを使います。
閾値も登録者の仮説であり、検証済みの犯罪モデルと呼びません。

## 司法の記録：judicial_records

| stage | 登録できるstatus |
|---|---|
| police | report_received、report_refused、investigation、protection |
| prosecutor | prosecution、non_prosecution |
| court | acquittal、conviction |
| oversight | complaint、investigation、finding |

corruption_statusはnot_assessed/hypothesis/documented。
not_assessedならcorruption_evidence_idsは空。それ以外は根拠必須。
documentedにはcourt_findingに分類した根拠を要求します。この分類自体は登録者の入力で、判決内容の自動認定ではありません。
不起訴・無罪から腐敗を自動で作る処理はありません。case_idで案件をひも付け、新情報は新しいレコードIDにします。
reasonとalternativeに判断理由・別の説明・不足資料を記載してください。

## 結果判定・通知

resolve入力は`examples/risk-resolution.json`参照。期限前の確定や既存結果の改変は禁止。
negativeにはcoverage_complete=trueと根拠が必要。無記録だけで被害なしとしないための登録上の制約です。
現時点では、通知からの見逃し率・適中率の自動集計は未実装。シナリオの結果と通知履歴をexportして評価します。

evaluateはBEGIN IMMEDIATEで処理し、状態・版・根拠が同じ通知を抑止します。
評価と通知は同じトランザクション。同じDBで評価時刻を逆行させることは禁止。
DBのavailable_atは入力された情報時刻、recorded_atは実際の登録時刻。前向き評価では両方を確認してください。
HTML/JSONは原文や司法記録を含みます。実データの出力は公開用に匿名化・利用条件の確認が必要です。
