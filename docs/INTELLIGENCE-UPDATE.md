# 横断収集更新

## 起動

```
py -m wpe.autonomous once --config config/intelligence.json
py -m wpe.autonomous loop --config config/intelligence.json
```

統合画面 `outputs/intelligence.html`、関連探索 `outputs/relationships/index.html`、経済予測 `outputs/economic/index.html` を生成する。数値・関連レポートは取得試行時に再計算し、待機中は前の結果を保持する。最初の実行は既存の全地域収集が未導入の場合、ページ取得に時間がかかる。途中の `collecting` / `ok` / `failed` に収集先を表示する。Ctrl+C は実行中の取得・サイクルが終わるまで待つ場合がある。

## 既定設定（49収集先）

- 既存6収集先。
- 世界銀行22指標・全対象地域。1990年以降、週1回。犯罪は故意殺人指標であり全犯罪ではない。軍事費・軍人員、政府経費・歳入・税収・中央政府債務、国際移民ストック・難民を追加。政府経費は各国の予算案や全支出の完全な再現ではない。指標・地域により欠損がある。
- Eurostat 3データセット：crim_off_cat / crim_just_ctz / crim_pris_ctz。2000年以降、週1回。欧州の対象地域のみ。API寸法・単位・法律上の段階・国籍カテゴリーを保持する。国籍は出生地・在留資格・移民資格とは別。収容者を有罪者と扱わない。不明な法律段階を推測せず除外数を表示する。
- GDELT 9テーマ：紛争、軍備、外交、麻薬、犯罪、司法、政策、金融、宗教。毎時、過去24時間の最大250件。取得対象は報道見出しとリンクで、全文や実事件データではない。出版元の所在地を発生国と扱わない。seendateは索引時刻なので公表時刻を捏造しない。最大件数に到達すると打ち切りを表示する。英語の検索語を使用しており、全言語・全報道の完全網羅ではない。
- SEC 9収集先：CIK 1067983 / 19617 / 886982 の届出索引（毎日）、Assets / Liabilities（週1回）。SECレスポンスでCIKを照合し主体名を取得する。索引に13F/13D/Gがあることと保有内容を解析したことは別。資産・負債は貸借対照表上の額であり資金移動ではない。古い届出の別ファイルは取得せず、その数を表示する。

## SEC接続の有効化

PowerShellで起動前に設定する。実際に連絡可能なアドレスを使う。設定は当該PowerShellプロセスとそこから起動した子プロセスに引き継がれ、Gitには書かない。

```
$env:WPE_SEC_USER_AGENT = "WPE research YourName your-contact-address"
```

この文字列をSECのHTTP User-Agentとして送信する。未設定なら接続せず `not_configured` として統合画面・latest.jsonに表示する。SECの公開APIにはキーは不要だが利用方針に沿った識別を行う。

## 登録・マッピングが必要なデータ

政策の交付実績・受益者、株主・融資・政治献金などの具体的資金関係、宗教人口・社会規範の調査値、国籍と異なる定義の移民犯罪、麻薬押収量などの数値は、次のJSON入力またはmapped CSV/JSON収集器を使う。これらに一律の架空値を入れない。既定設定だけですべてを自動抽出した状態ではない。

```
py -m wpe.domain_sources import your-records.json --db data/runtime.sqlite
py -m wpe.domain_sources export outputs/domain-records.json --db data/runtime.sqlite
```

書き込み時は収集ループを停止する。既存DBにはCREATE TABLEで追加し、既存テーブルを削除しない。インポートはまとめて検証し、途中でエラーがあれば全件を戻す。同一IDは不変。修正は新IDを使用し、notesに修正対象を記録する。

### レコード

最上位 `{"records": [...]}`。空テンプレート `examples/domain-input/empty.json`。

必須：id, domain, kind, known_on, source_url, source_name, title, definition。

- domain: crime / justice / corporate / policy / religion / education / society / migration / drugs / conflict / military / diplomacy / budget / finance
- kind: statistic / survey / article / filing / relationship
- source_url: 出典のHTTPS URL。source_name: 発行主体。definition: 測定内容または主張の定義。
- known_on: 取得して知り得た日。occurred_on: 観測期間末または出来事の日。公表日に勝手に置き換えない。
- statistic / survey: value（有限数）, unit, population, classification, region, occurred_onが必須。denominatorは正数またはnull。
- stage: reported / suspect / arrest / prosecution / conviction / non_prosecution / acquittal / prisoner / victim_survey / seizure / death / appropriation / expenditure / filing / survey / unknown。犯罪・司法・移民・麻薬の数値ではunknownを認めない。
- claim_status: report / official_record / hypothesis / reviewed。official_recordは真実の認定ではなく記録の種類。
- period_start: 本当に1月1日～12月31日の年間値なら設定。年末時点のストック値などに1年間を捏造しない。
- entities: `[{"id":"識別子","name":"公開主体名"}]`
- relations: `[{"from":"主体ID","to":"対象ID","relation":"receives_subsidy","status":"report","quote":"原文引用"}]`
- source_text: 引用を含む出典テキスト。関係のquoteが本文に含まれることを検証する。本文の真偽や意味が自動で検証されるわけではない。
- relation: owns / invests_in / lends_to / receives_subsidy / awarded_contract / influences / anticipates / benefits_from / burdened_by / reports_managed_position
- notes: 制限・修正経緯など。

年次の関連分析は同一地域で単位・母集団・段階・定義・出典を分ける。比較対象の異なる年次記録を勝手に混ぜない。20組未満は計算しない。故意殺人と検挙者、国籍と移民資格、宗教人口と犯罪者を同一概念として扱わない。

### mapped収集先

config/intelligence.jsonのsourcesへ追加できる。最大50収集先なので既定49件から多く追加する場合は不要な収集先を除く。

必須：id, type="mapped", url（公開HTTPS CSV/JSON）, format="csv"または"json", interval_seconds, columns, defaults。

columnsは `{"value":"元ファイルの値列","occurred_on":"元ファイルの期間末列"}` のように指定。defaultsにはdomain / kind / source_url / source_name / title / definitionなど定義を指定。known_onとidは収集器で生成。JSONのrecords_pathは `['data','records']` のようなキー列。CSVはUTF-8（BOMも可）。APIキーが必要な元データや独自の帳票は追加アダプターが必要。

## 検証範囲

105件のテスト、コンパイル、差分チェックをローカルPython環境で実施。HTTPリクエストを置き換えたfixtureで取得処理、原文照合、同一ID、将来時刻、法律段階、寸法解釈、失敗分離、自動レポートを確認。新収集先の実通信・全データ規模・Windows動作は利用環境で別途確認が必要。世界情勢の多変量予測や因果推定が完成した状態ではない。

## 出典仕様

- https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- https://www.sec.gov/about/developer-resources
- https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-detailed-guidelines/api-statistics
- https://ec.europa.eu/eurostat/databrowser/view/crim_off_cat/default/table?lang=en
- https://ec.europa.eu/eurostat/databrowser/view/crim_just_ctz/default/table?lang=en
- https://ec.europa.eu/eurostat/databrowser/view/crim_pris_ctz/default/table?lang=en
- https://datahelpdesk.worldbank.org/knowledgebase/articles/898599-indicator-api-queries
