/**
 * SyncVey — ダウンロード計測スクリプト
 *
 * 設定手順:
 * 1. Google スプレッドシートを新規作成
 * 2. 拡張機能 → Apps Script を開く
 * 3. このコードを貼り付けて保存
 * 4. デプロイ → 新しいデプロイ → 種類: ウェブアプリ
 *    - 次のユーザーとして実行: 自分
 *    - アクセスできるユーザー: 全員
 * 5. デプロイURLをコピーして survey.ja.html / survey.en.html の
 *    SCRIPT_URL に貼り付ける
 *
 * 既存シートに列を足す場合: このコードを貼り直して再デプロイすれば、
 * 次の投稿時に doPost がヘッダ行を新しい7列に自動補修する（既存データは温存）。
 */

// 列の順番＝この配列の通り。列を増やすときはここに足す。
var HEADER = ['Timestamp', 'Country', 'State / Prefecture', 'Lang', 'Source', 'Referrer', 'Note'];

function doPost(e) {
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    ensureHeader_(sheet);

    let data = {};
    if (e.postData && e.postData.contents) {
      data = JSON.parse(e.postData.contents);
    }

    sheet.appendRow([
      new Date(),
      data.country  || '',
      data.state    || '',
      data.lang     || '',
      data.source   || '',
      data.referrer || '',
      data.note     || '',
    ]);

    return ContentService
      .createTextOutput('ok')
      .setMimeType(ContentService.MimeType.TEXT);

  } catch (err) {
    return ContentService
      .createTextOutput('error: ' + err.toString())
      .setMimeType(ContentService.MimeType.TEXT);
  }
}

/**
 * ヘッダ行を保証する。空シートなら作成、既存シートで列が足りなければ
 * 1行目を新しいヘッダに補修する（A〜D の既存ラベルはそのまま、E〜G を追加）。
 */
function ensureHeader_(sheet) {
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADER);
    return;
  }
  const firstRow = sheet.getRange(1, 1, 1, HEADER.length).getValues()[0];
  if (firstRow[HEADER.length - 1] !== HEADER[HEADER.length - 1]) {
    sheet.getRange(1, 1, 1, HEADER.length).setValues([HEADER]);
  }
}

// テスト用（Apps Script エディタ上で実行して動作確認）
function testPost() {
  const mockEvent = {
    postData: {
      contents: JSON.stringify({
        country: 'Canada', state: 'Ontario', lang: 'en',
        source: 'dev.to', referrer: 'https://dev.to/hitoshi1964/...', note: 'want console-drift detection'
      })
    }
  };
  const result = doPost(mockEvent);
  Logger.log(result.getContent());
}
