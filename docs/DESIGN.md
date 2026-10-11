# システム設計と実装範囲

## 目的

複数分野の観測を追加し、定義済みの未来イベントについて継続的に予測・評価する。
全ての出来事を観測することも、未観測の原因を排除することもできない。
データ量の増加が精度向上になるかは別の検証課題である。

## v0.1のデータモデル

| テーブル | 内容 | 保持方針 |
|---|---|---|
| observations | 変数・地域・対象日・既知日・値・出典・モード | 公表時点ごとの値を保存、同一vintageの矛盾は拒否 |
| predictions | 基準日・期限・対象・閾値・確率・版・snapshot・記録日時 | 新しい予測は追加、過去予測を上書きしない |
| outcomes | 予測ID・初回判定日・結果・Brier・根拠ID | 初回確定を保存、後日の改訂で無断変更しない |

スキーマの実体はwpe/core.pyのconnect関数。SQLiteを使い追加のDBセットアップなしで動く。
予測snapshotには特徴量、モデル成分、類似窓、使用した全vintageのIDを保存する。
`created_on`は予測の情報基準日、`recorded_at`は実際の記録日時。
後から計算した過去日の予測を、その時点で発行した予測実績として数えてはいけない。
DBの直接編集まで防ぐセキュリティ機構ではない。将来は権限分離・署名・WORM保存を追加する。

## 実装されている計算

1. 観測・既知日の両方が情報基準日以下のvintageを取得。
2. 各対象日の最新既知vintageを選び、後方3観測平均を計算。
3. 不等間隔の日数で一次・二次差分を計算。Zスコアと古さを出力。
4. 学習窓の特徴量を当時の情報だけで再構築。閾値超過ラベルは現在までに判明したものを使う。
5. 学習セットの分散でスケーリングし、ユークリッド距離で15類似窓を選択。
6. (1+発生数)/(2+窓数)でベースレートと類似窓確率を計算、50:50平均。
7. 予測と根拠を保存。期限後にデータが揃えばBrier=(p-y)^2を計算。
8. 時系列順バックテスト、ベースレートとの比較、校正ビンを出力。

Brierは小さいほど良い。skill=1-Brier_model/Brier_baseline。
負のskillなら、このテストではベースレートより悪い。
合成データでの良い成績は実世界の予測性能を証明しない。
稀なイベントでは全て起こらないとする予測のBrierも良くなり得るため、
実運用ではPR曲線・校正・リードタイム・意思決定の費用を合わせて評価する。
類似度は「歴史が同じ展開を辿る確率」ではない。

## データ源の追加計画

| 分野 | 候補 | v0.1での対応 | 次に確認すること |
|---|---|---|---|
| マクロ | World Bank | 現行値取得実装、モック検証 | 当時のvintage、改訂、公表日、欠測 |
| 米国経済・金融 | FRED / ALFRED | 未接続 | API認証とvintageの利用条件 |
| 紛争 | UCDP / VIEWS / ACLED | 未接続 | イベント定義・公表遅れ・アクセス契約 |
| ニュース | GDELT / 公的RSS | 未接続 | 重複・報道量・言語・地域バイアス |
| 日本 | e-Stat / 日銀 | CSVで取り込み可能 | 公表日時・単位・季節調整・改訂 |
| 食料・エネルギー | FAO / EIA等 | 未接続 | 頻度・更新遅れ・利用許諾 |

公式資料:
- World Bank API: https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures
- FRED API: https://fred.stlouisfed.org/docs/api/fred/
- VIEWS: https://viewsforecasting.org/
- GDELT: https://www.gdeltproject.org/

## 拡張スキーマ案（まだ未実装）

- variables(id, domain, unit, frequency, aggregation, owner)
- sources(id, url, license, fetch_schedule, publication_lag)
- raw_documents(id, url, fetched_at, published_at, hash, storage_path)
- events(id, occurred_at, published_at, actors, region, type, evidence_ids, extraction_version)
- event_links(event_id, variable_id, relation_type, evidence_status)
- questions(id, definition, region, horizon, resolution_source, revision_policy)
- model_runs(id, version, feature_version, training_cutoff, evaluation_fold)
- forecast_updates(id, question_id, probability, information_cutoff, run_id)

LLMは文書から引用付きイベントや仮説を抽出する。
情報の重複や誤報を処理してから特徴量化する。LLMが述べた因果を確定事項として保存しない。
相関だけでは因果関係を証明できない。因果推論には識別仮定・交絡・介入を検討する。
Monte Carloの試行数を増やしても仮定が誤っていれば改善しない。
戦争・不況・AI成長は同時に起こり得るため、単純に排他的なシナリオとして合計100%にしない。

## 運用の順序

公表時点データを揃える → 判定可能な少数の問題を登録 → ベースラインと比較 →
一定期間の前向き予測を採点 → 有効な分野・モデルのみ拡張。
初期は数十系列でもよいが、欠測や頻度の違いを無理に毎日補間しない。
日次・月次・年次の各観測時刻と公表時刻を維持する。
データ数・最終更新日・出典・失敗した取得・未解決数をレポートする運用を追加する。
常時運転のホストとスケジューラはこのZIPには含まれない。
