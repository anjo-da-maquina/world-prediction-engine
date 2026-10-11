# GitHubへのアップロード

この版はローカルで動くソース一式です。GitHubでの作成・pushはまだ実行していません。
以下はGitをインストールし、GitHubアカウントを用意した後の手順です。

## 1. ソースを開く

ZIPを解凍してworld-prediction-engineフォルダに入り、まずテストとデモを実行します。
READMEの手順を使用してください。

## 2. ローカルの履歴を作る

```bash
git init -b main
git add .
git status --short
```

一覧にはソース、設定例、架空例、ドキュメントだけが入ることを確認します。
.gitignoreがDB、出力、環境変数を除外しますが、別名の秘密ファイルは自動で判断できません。

名前・メールが未設定の場合だけ、次の値を自分の設定へ置き換えて実行します。
GitHubの非公開メールアドレスも利用できます。これはこのリポジトリだけへの設定です。

```bash
git config user.name "YOUR_NAME"
git config user.email "YOUR_GITHUB_EMAIL"
git commit -m "Initial WPE research prototype"
```

## 3. GitHubに空のリポジトリを作る

GitHubのNew repositoryで名前をworld-prediction-engineなどにします。
最初はPrivateで動作や内容を確認することを推奨します。
README・.gitignore・Licenseの初期生成は選ばず、空の状態で作成します。この一式にREADMEと.gitignoreがあります。
共有範囲と配布ライセンスは所有者が決めます。

## 4. 接続してpushする

YOUR_ACCOUNTとYOUR_REPOSITORYをGitHub上の名前に置き換えます。

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

認証を求められたらGitHubの認証方法に従ってください。パスワードやトークンをソースに書かないでください。
既存リポジトリへの接続の場合は、先にその履歴を取得して統合します。強制pushで上書きしません。

## 5. Actionsを確認する

リポジトリのActionsでCIの結果を確認します。
Python 3.10/3.12/3.13のテストと架空デモを実行します。外部APIへの通信はCIには含みません。
通知・常時収集サービスは、リポジトリへ上げただけでは起動しません。

## 6. 更新する

変更後にテストを実行し、差分を確認してコミットします。

```bash
git diff
git add .
git commit -m "Describe the actual change"
git push
```

実データのDB・原文・レポートは別にバックアップし、Gitのソース履歴と混同しないでください。
