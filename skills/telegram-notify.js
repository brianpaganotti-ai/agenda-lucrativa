/**
 * skill: telegram-notify
 * OpenSquad Skill — envia mensagem para o usuário via Telegram Bot API.
 *
 * Uso nos squads:
 *   skill: telegram-notify
 *   prompt: >
 *     Envie via skill telegram-notify a mensagem: "Squad concluído! ✅"
 *
 * Variáveis de ambiente necessárias na VM:
 *   TELEGRAM_BOT_TOKEN  — token do bot (@BotFather)
 *   TELEGRAM_CHAT_ID    — chat_id do usuário (obtido via /start no bot)
 *
 * Alternativamente, os valores são lidos do Secret Manager se o
 * GOOGLE_APPLICATION_CREDENTIALS estiver configurado.
 */

import { execSync } from "child_process";
import { readFileSync } from "fs";

// ---------------------------------------------------------------------------
// Configuração
// ---------------------------------------------------------------------------

const PROJECT_ID =
  process.env.PROJECT_ID || "project-87c1c65b-10d3-40d5-999";

function readSecret(secretName) {
  try {
    const result = execSync(
      `gcloud secrets versions access latest --secret="${secretName}" --project="${PROJECT_ID}" 2>/dev/null`,
      { encoding: "utf8" }
    );
    return result.trim();
  } catch {
    return null;
  }
}

function getBotToken() {
  return (
    process.env.TELEGRAM_BOT_TOKEN || readSecret("telegram-bot-token") || ""
  );
}

function getChatId() {
  return (
    process.env.TELEGRAM_CHAT_ID ||
    readSecret("telegram-allowed-user-id") ||
    ""
  );
}

// ---------------------------------------------------------------------------
// Envio de mensagem
// ---------------------------------------------------------------------------

/**
 * Envia uma mensagem de texto para o usuário no Telegram.
 * @param {string} text — texto da mensagem (suporta MarkdownV2)
 * @param {boolean} useMarkdown — se true, usa parse_mode=MarkdownV2
 */
export async function sendMessage(text, useMarkdown = false) {
  const token = getBotToken();
  const chatId = getChatId();

  if (!token || !chatId) {
    throw new Error(
      "telegram-notify: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID são obrigatórios. " +
        "Defina como variável de ambiente ou crie os secrets no Secret Manager."
    );
  }

  const body = {
    chat_id: chatId,
    text: text.slice(0, 4096), // limite do Telegram
    ...(useMarkdown && { parse_mode: "MarkdownV2" }),
  };

  const response = await fetch(
    `https://api.telegram.org/bot${token}/sendMessage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }
  );

  const data = await response.json();

  if (!data.ok) {
    throw new Error(`telegram-notify: API error — ${data.description}`);
  }

  return data.result;
}

/**
 * Envia um arquivo de texto como documento para o Telegram.
 * Útil para outputs longos.
 * @param {string} filePath — caminho absoluto do arquivo
 * @param {string} caption — legenda opcional
 */
export async function sendDocument(filePath, caption = "") {
  const token = getBotToken();
  const chatId = getChatId();

  if (!token || !chatId) {
    throw new Error("telegram-notify: credenciais não configuradas.");
  }

  const fileContent = readFileSync(filePath);
  const fileName = filePath.split("/").pop();

  const formData = new FormData();
  formData.append("chat_id", chatId);
  formData.append("document", new Blob([fileContent]), fileName);
  if (caption) formData.append("caption", caption.slice(0, 1024));

  const response = await fetch(
    `https://api.telegram.org/bot${token}/sendDocument`,
    { method: "POST", body: formData }
  );

  const data = await response.json();
  if (!data.ok) {
    throw new Error(`telegram-notify: sendDocument error — ${data.description}`);
  }

  return data.result;
}

// ---------------------------------------------------------------------------
// Entrada da skill (chamada pelo OpenSquad)
// ---------------------------------------------------------------------------

/**
 * Ponto de entrada principal da skill.
 * O OpenSquad chama esta função passando o contexto do step atual.
 *
 * @param {object} context — contexto fornecido pelo OpenSquad
 * @param {string} context.message — mensagem a enviar (extraída do prompt)
 * @param {string} [context.file] — arquivo opcional a enviar como documento
 */
export async function run(context) {
  const { message, file } = context || {};

  if (!message && !file) {
    throw new Error("telegram-notify: forneça 'message' ou 'file' no contexto.");
  }

  const results = [];

  if (message) {
    const result = await sendMessage(String(message));
    results.push({ type: "message", message_id: result.message_id });
    console.log(`[telegram-notify] Mensagem enviada: ID ${result.message_id}`);
  }

  if (file) {
    const result = await sendDocument(file, message || "");
    results.push({ type: "document", message_id: result.message_id });
    console.log(`[telegram-notify] Documento enviado: ${file}`);
  }

  return { success: true, results };
}

// ---------------------------------------------------------------------------
// CLI standalone — para teste direto
// ---------------------------------------------------------------------------

// node skills/telegram-notify.js "Olá do OpenSquad!"
if (process.argv[1] === new URL(import.meta.url).pathname) {
  const msg = process.argv[2] || "Teste do skill telegram-notify ✅";
  sendMessage(msg)
    .then((r) => console.log("Enviado:", r.message_id))
    .catch((e) => {
      console.error("Erro:", e.message);
      process.exit(1);
    });
}
