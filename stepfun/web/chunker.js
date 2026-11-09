/**
 * 本地切块：段落/句子优先，块间保留 overlap 个字符的重叠，
 * 让跨句的答案不至于被切断。纯前端计算，不消耗任何服务端资源。
 */
export function chunkDocument(doc, options = {}) {
  const maxChars = options.maxChars ?? 480;
  const overlap = options.overlap ?? 80;
  const text = String(doc.text ?? '').replace(/\r\n?/g, '\n').trim();
  if (!text) return [];

  const title = doc.title ?? firstHeading(text) ?? doc.docId ?? '未命名文档';
  const body = stripFirstHeading(text);
  const pieces = splitPieces(body, maxChars);
  const chunks = [];
  let buffer = '';
  let index = 0;

  const push = (chunkText) => {
    const trimmed = chunkText.trim();
    if (!trimmed) return;
    chunks.push({
      id: `${doc.docId}#${index}`,
      docId: doc.docId,
      title,
      source: doc.source ?? '',
      chunkIndex: index,
      text: trimmed,
    });
    index += 1;
  };

  for (const piece of pieces) {
    if (buffer && buffer.length + piece.length + 1 > maxChars) {
      push(buffer);
      buffer = overlap > 0 ? tail(buffer, overlap) : '';
    }
    buffer = buffer ? `${buffer}\n${piece}` : piece;
  }
  push(buffer);
  return chunks;
}

/** 长文本切成不超过 maxChars 的句子片段。 */
function splitPieces(text, maxChars) {
  const sentences = text
    .split(/(?<=[。！？!?；;])|\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean);
  const pieces = [];
  for (const sentence of sentences) {
    if (sentence.length <= maxChars) {
      pieces.push(sentence);
      continue;
    }
    for (let offset = 0; offset < sentence.length; offset += maxChars) {
      pieces.push(sentence.slice(offset, offset + maxChars));
    }
  }
  return pieces;
}

function tail(text, length) {
  const sliced = text.slice(Math.max(0, text.length - length));
  const boundary = sliced.search(/[。！？!?；;\n]/);
  return boundary >= 0 ? sliced.slice(boundary + 1).trim() : sliced.trim();
}

function firstHeading(text) {
  const match = text.match(/^#{1,6}\s+(.+)$/m);
  return match ? match[1].trim() : null;
}

function stripFirstHeading(text) {
  return text.replace(/^#{1,6}\s+.+\n?/, '').trim();
}

export function chunkAll(docs, options = {}) {
  const chunks = [];
  for (const doc of docs) chunks.push(...chunkDocument(doc, options));
  return chunks;
}
