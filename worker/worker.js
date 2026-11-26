// Cloudflare Worker：接收 Discord 的 /usage 斜線指令，觸發 GitHub Actions 立刻跑一次報告。
//
// 需要在 Worker 設定的變數（Settings → Variables and Secrets）：
//   DISCORD_PUBLIC_KEY      Discord Application 的 Public Key（General Information 頁）
//   DISCORD_APPLICATION_ID  Discord Application ID
//   GITHUB_TOKEN            fine-grained token，只選這個 repo，權限 Actions: Read and write（設成 Secret）
//   GITHUB_REPO             例如 illillli/claude-usage-discord-bot
//   ALLOWED_USER_ID         你的 Discord 使用者 ID，只有你能觸發（留空 = 任何人都能用）

const PING = 1;
const APPLICATION_COMMAND = 2;
const PONG = 1;
const CHANNEL_MESSAGE = 4;
const DEFERRED_CHANNEL_MESSAGE = 5;
const EPHEMERAL = 64; // 只有下指令的人看得到

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

async function verify(request, body, publicKey) {
  const sig = request.headers.get("X-Signature-Ed25519");
  const ts = request.headers.get("X-Signature-Timestamp");
  if (!sig || !ts) return false;
  try {
    const key = await crypto.subtle.importKey("raw", hexToBytes(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, hexToBytes(sig), new TextEncoder().encode(ts + body));
  } catch {
    return false;
  }
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

async function editReply(env, token, content) {
  await fetch(`https://discord.com/api/v10/webhooks/${env.DISCORD_APPLICATION_ID}/${token}/messages/@original`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "User-Agent": "DiscordBot (https://github.com/illillli/claude-usage-discord-bot, 1.0)",
    },
    body: JSON.stringify({ content }),
  });
}

async function dispatch(env, token) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/daily.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "claude-usage-discord-bot-worker",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: { force: "true", interaction_token: token, application_id: env.DISCORD_APPLICATION_ID },
    }),
  });
  if (!res.ok) {
    const detail = (await res.text()).slice(0, 300);
    await editReply(env, token, `⚠️ 觸發 GitHub Actions 失敗：HTTP ${res.status}\n\`\`\`${detail}\`\`\``);
  }
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok");
    const body = await request.text();
    if (!(await verify(request, body, env.DISCORD_PUBLIC_KEY))) {
      return new Response("invalid request signature", { status: 401 });
    }
    const interaction = JSON.parse(body);

    if (interaction.type === PING) return json({ type: PONG });

    if (interaction.type === APPLICATION_COMMAND && interaction.data?.name === "usage") {
      const userId = interaction.member?.user?.id ?? interaction.user?.id;
      if (env.ALLOWED_USER_ID && userId !== env.ALLOWED_USER_ID) {
        return json({ type: CHANNEL_MESSAGE, data: { content: "這個指令只有 bot 主人能用。", flags: EPHEMERAL } });
      }
      // Discord 規定 3 秒內要回應：先回「思考中…」，再在背景觸發 GitHub
      ctx.waitUntil(dispatch(env, interaction.token));
      return json({ type: DEFERRED_CHANNEL_MESSAGE, data: { flags: EPHEMERAL } });
    }

    return json({ type: CHANNEL_MESSAGE, data: { content: "不認得這個指令。", flags: EPHEMERAL } });
  },
};
