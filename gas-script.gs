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
 */

function doPost(e) {
  try {
    const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();

    // ヘッダー行がなければ追加
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['Timestamp', 'Country', 'State / Prefecture', 'Lang']);
    }

    let country = '';
    let state = '';
    let lang = '';

    if (e.postData && e.postData.contents) {
      const data = JSON.parse(e.postData.contents);
      country = data.country || '';
      state   = data.state   || '';
      lang    = data.lang    || '';
    }

    sheet.appendRow([new Date(), country, state, lang]);

    return ContentService
      .createTextOutput('ok')
      .setMimeType(ContentService.MimeType.TEXT);

  } catch (err) {
    return ContentService
      .createTextOutput('error: ' + err.toString())
      .setMimeType(ContentService.MimeType.TEXT);
  }
}

// テスト用（Apps Script エディタ上で実行して動作確認）
function testPost() {
  const mockEvent = {
    postData: {
      contents: JSON.stringify({ country: 'Japan', state: '東京都', lang: 'ja' })
    }
  };
  const result = doPost(mockEvent);
  Logger.log(result.getContent());
}
