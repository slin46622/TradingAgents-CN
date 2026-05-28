<template>
  <div class="telegram-config">
    <div class="page-header">
      <h2>Telegram 通知配置</h2>
      <p class="page-desc">配置 Telegram Bot 接收 AI 交易信号推送，并支持回复「确认」触发模拟下单</p>
    </div>

    <el-card shadow="never" class="config-card">
      <template #header>
        <span>Bot 连接设置</span>
      </template>
      <el-form :model="form" label-width="130px" style="max-width: 560px">
        <el-form-item label="Bot Token">
          <el-input
            v-model="form.bot_token"
            placeholder="请输入 Telegram Bot Token（如 123456789:ABC...）"
            show-password
            clearable
          />
          <div class="form-hint">
            通过 <a href="https://t.me/BotFather" target="_blank">@BotFather</a> 创建 Bot 获取 Token
          </div>
        </el-form-item>
        <el-form-item label="Chat ID">
          <el-input
            v-model="form.chat_id"
            placeholder="请输入 Chat ID（数字或 @频道名）"
            clearable
          />
          <div class="form-hint">
            向 <a href="https://t.me/userinfobot" target="_blank">@userinfobot</a> 发消息获取你的 Chat ID
          </div>
        </el-form-item>
        <el-form-item label="启用通知">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
          <el-button :loading="testing" @click="testConnection">测试连接</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="currentConfig.configured" shadow="never" class="status-card">
      <template #header><span>当前状态</span></template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="Bot Token">{{ currentConfig.bot_token_masked }}</el-descriptions-item>
        <el-descriptions-item label="Chat ID">{{ currentConfig.chat_id }}</el-descriptions-item>
        <el-descriptions-item label="通知开关">
          <el-tag :type="currentConfig.enabled ? 'success' : 'info'">
            {{ currentConfig.enabled ? '已启用' : '已禁用' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" class="guide-card">
      <template #header><span>使用说明</span></template>
      <ol class="guide-list">
        <li>打开 Telegram，搜索并联系 <strong>@BotFather</strong>，发送 <code>/newbot</code> 创建机器人，获取 Bot Token</li>
        <li>向你的 Bot 发送任意消息以激活会话</li>
        <li>通过 <strong>@userinfobot</strong> 获取你的 Chat ID（数字格式）</li>
        <li>在上方填写配置后点击「测试连接」，收到确认消息即配置成功</li>
        <li>AI 分析完成后，Bot 将自动推送交易信号；回复「<strong>确认</strong>」触发模拟下单，回复「<strong>忽略</strong>」跳过</li>
      </ol>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

interface TelegramConfig {
  configured: boolean
  bot_token_masked?: string
  chat_id?: string
  enabled?: boolean
}

const saving = ref(false)
const testing = ref(false)
const currentConfig = reactive<TelegramConfig>({ configured: false })

const form = reactive({
  bot_token: '',
  chat_id: '',
  enabled: true,
})

onMounted(async () => {
  try {
    const res = await axios.get('/api/telegram/config')
    if (res.data?.data?.configured) {
      Object.assign(currentConfig, res.data.data)
    }
  } catch {
    // not configured yet
  }
})

async function saveConfig() {
  if (!form.bot_token.trim() || !form.chat_id.trim()) {
    ElMessage.warning('请填写 Bot Token 和 Chat ID')
    return
  }
  saving.value = true
  try {
    await axios.post('/api/telegram/config', form)
    ElMessage.success('配置已保存')
    // refresh status
    const res = await axios.get('/api/telegram/config')
    if (res.data?.data) Object.assign(currentConfig, res.data.data)
    form.bot_token = ''
  } catch {
    ElMessage.error('保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  try {
    const res = await axios.post('/api/telegram/test')
    if (res.data?.data?.connected) {
      ElMessage.success('连接成功！请查看 Telegram 中的确认消息')
    } else {
      ElMessage.error('连接失败，请检查 Token 和 Chat ID 是否正确')
    }
  } catch (err: any) {
    const msg = err?.response?.data?.detail || '请求失败，请先保存配置'
    ElMessage.error(msg)
  } finally {
    testing.value = false
  }
}
</script>

<style scoped>
.telegram-config {
  padding: 20px;
  max-width: 800px;
}

.page-header {
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
}

.page-desc {
  color: #909399;
  margin: 0;
  font-size: 13px;
}

.config-card,
.status-card,
.guide-card {
  margin-bottom: 16px;
}

.form-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;

  a {
    color: var(--el-color-primary);
  }
}

.guide-list {
  padding-left: 20px;
  line-height: 2;
  color: #606266;
  font-size: 14px;

  code {
    background: #f5f7fa;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 12px;
  }
}
</style>
