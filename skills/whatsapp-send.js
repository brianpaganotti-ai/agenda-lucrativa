/**
 * skill: whatsapp-send
 * OpenSquad Skill — envia mensagem WhatsApp via API Flask local (porta 8080)
 * ou diretamente via Meta WhatsApp Business API.
 *
 * Uso nos squads:
 *   skill: whatsapp-send
 *   prompt: >
 *     Envie via skill whatsapp-send:
 *       telefone: +5511999999999
 *       mensagem: "Olá! Vi seu negócio e..."
 *
 * Variáveis de ambiente necessárias:
 *   WHATSAPP_TOKEN          — token da Meta Business API
 *   WHATSAPP_PHONE_NUMBER_ID — ID do número remetente
 *
 * Ou via Secret Manager:
 *   whatsapp-token
 *   whatsapp-phone-number-id
 */

import { execSync } from "child_process";

const PROJECT_ID =
  process.env.PROJECT_ID || "project-87c1c65b-10d3-40d5-999";

// URL da API Flask local na VM (agenda-api.service)
const LOCAL_API_URL = process.env.AGENDA_API_URL || "http://localhost:8080";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readSecret(secretName) {
  try {
    return execSync(
      `gcloud secrets versions access latest --secret="${secretName}" --project="${PROJECT_ID}" 2>/dev/null`,
      { encoding: "utf8" }
    ).trim();
  } catch {
    return null;
  }
}

function getWaToken() {
  return process.env.WHATSAPP_TOKEN || readSecret("whatsapp-token") || "";
}

function getPhoneNumberId() {
  return (
    process.env.WHATSAPP_PHONE_NUMBER_ID ||
    readSecret("whatsapp-phone-number-id") ||
    ""
  );
}

/**
 * Normaliza número para formato E.164.
 * Exemplos: "11999999999" → "+5511999999999"
 *           "5511999999999" → "+5511999999999"
 *           "+5511999999999" → "+5511999999999"
 */
function normalizePhone(phone) {
  const digits = String(phone).replace(/\D/g, "");
  if (digits.startsWith("55") && digits.length >= 12) {
    return `+${digits}`;
  }
  if (digits.length === 11 || digits.length === 10) {
    return `+55${digits}`;
  }
  return `+${digits}`;
}

// ---------------------------------------------------------------------------
// Envio via Meta WhatsApp Business API
// ---------------------------------------------------------------------------

/**
 * Envia mensagem de texto via Meta WhatsApp Business API v20.
 * @param {string} to — número E.164 do destinatário
 * @param {string} text — texto da mensagem
 */
export async function sendWhatsApp(to, text) {
  const token = getWaToken();
  const phoneNumberId = getPhoneNumberId();

  if (!token || !phoneNumberId) {
    throw new Error(
      "whatsapp-send: WHATSAPP_TOKEN e WHATSAPP_PHONE_NUMBER_ID são obrigatórios. " +
        "Configure como variáveis de ambiente ou crie os secrets no Secret Manager."
    );
  }

  const normalizedTo = normalizePhone(to);

  const body = {
    messaging_product: "whatsapp",
    recipient_type: "individual",
    to: normalizedTo,
    type: "text",
    text: {
      preview_url: false,
      body: text.slice(0, 4096),
    },
  };

  const response = await fetch(
    `https://graph.facebook.com/v20.0/${phoneNumberId}/messages`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    }
  );

  const data = await response.json();

  if (data.error) {
    throw new Error(
      `whatsapp-send: Meta API error ${data.error.code} — ${data.error.message}`
    );
  }

  return {
    message_id: data.messages?.[0]?.id,
    to: normalizedTo,
    status: "sent",
  };
}

// ---------------------------------------------------------------------------
// Envio em lote com delay
// ---------------------------------------------------------------------------

/**
 * Envia mensagens para múltiplos destinatários com delay entre envios.
 * @param {Array<{telefone: string, mensagem: string}>} leads
 * @param {number} delayMs — delay em ms entre envios (padrão: 3000)
 */
export async function sendBatch(leads, delayMs = 3000) {
  const results = { success: [], failed: [] };

  for (const lead of leads) {
    try {
      const result = await sendWhatsApp(lead.telefone, lead.mensagem);
      results.success.push({ ...lead, ...result });
      console.log(
        `[whatsapp-send] ✅ Enviado para ${lead.telefone} (${lead.nome || "sem nome"})`
      );
    } catch (err) {
      results.failed.push({ ...lead, error: err.message });
      console.error(
        `[whatsapp-send] ❌ Falha ${lead.telefone}: ${err.message}`
      );
    }

    // Delay entre envios para evitar rate limiting
    if (delayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  console.log(
    `[whatsapp-send] Lote concluído: ${results.success.length} enviados, ${results.failed.length} falharam`
  );
  return results;
}

// ---------------------------------------------------------------------------
// Ponto de entrada da skill (chamado pelo OpenSquad)
// ---------------------------------------------------------------------------

/**
 * @param {object} context
 * @param {string} context.telefone — número do destinatário
 * @param {string} context.mensagem — texto da mensagem
 * @param {Array}  [context.lote]   — array de {telefone, mensagem} para envio em lote
 * @param {number} [context.delay]  — delay entre envios em ms (padrão: 3000)
 */
export async function run(context) {
  const { telefone, mensagem, lote, delay = 3000 } = context || {};

  // Envio em lote
  if (lote && Array.isArray(lote)) {
    return sendBatch(lote, delay);
  }

  // Envio individual
  if (!telefone || !mensagem) {
    throw new Error(
      "whatsapp-send: forneça 'telefone' e 'mensagem', ou 'lote' com array de destinatários."
    );
  }

  const result = await sendWhatsApp(telefone, mensagem);
  return { success: true, ...result };
}

// ---------------------------------------------------------------------------
// CLI standalone — para teste
// ---------------------------------------------------------------------------

// node skills/whatsapp-send.js +5511999999999 "Mensagem de teste"
if (process.argv[1] === new URL(import.meta.url).pathname) {
  const to = process.argv[2];
  const msg = process.argv[3] || "Teste do skill whatsapp-send ✅";

  if (!to) {
    console.error("Uso: node skills/whatsapp-send.js <telefone> <mensagem>");
    process.exit(1);
  }

  sendWhatsApp(to, msg)
    .then((r) => console.log("Enviado:", JSON.stringify(r, null, 2)))
    .catch((e) => {
      console.error("Erro:", e.message);
      process.exit(1);
    });
}
