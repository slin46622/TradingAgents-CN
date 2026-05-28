<template>
  <div class="live-trading">
    <div class="page-header">
      <h2>Binance 实盘交易</h2>
      <p class="page-desc">通过 Binance API 进行加密货币现货交易。请谨慎操作，实盘交易涉及真实资金。</p>
    </div>

    <!-- API 配置卡 -->
    <el-card shadow="never" class="config-card">
      <template #header>
        <div class="card-header">
          <span>API 配置</span>
          <el-tag v-if="configStatus.configured" type="success" size="small">已配置</el-tag>
          <el-tag v-else type="info" size="small">未配置</el-tag>
        </div>
      </template>
      <el-form :model="configForm" label-width="110px" style="max-width:540px">
        <el-form-item label="API Key">
          <el-input v-model="configForm.api_key" placeholder="Binance API Key" show-password clearable />
        </el-form-item>
        <el-form-item label="API Secret">
          <el-input v-model="configForm.api_secret" placeholder="Binance API Secret" show-password clearable />
        </el-form-item>
        <el-form-item label="启用实盘">
          <el-switch v-model="configForm.enabled" />
          <span class="form-hint" style="margin-left:8px">关闭后仍可查看账户，但无法下单</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="savingConfig" @click="saveConfig">保存配置</el-button>
          <el-button :loading="testingConn" @click="testConnection">测试连接</el-button>
        </el-form-item>
      </el-form>
      <div v-if="configStatus.configured" class="config-hint">
        当前 Key：{{ configStatus.api_key_masked }}
      </div>
    </el-card>

    <!-- 账户余额 -->
    <el-card v-if="configStatus.configured" shadow="never" class="account-card">
      <template #header>
        <div class="card-header">
          <span>账户余额</span>
          <el-button size="small" :loading="loadingAccount" @click="loadAccount">刷新</el-button>
        </div>
      </template>
      <el-table :data="balances" size="small" stripe>
        <el-table-column prop="asset" label="资产" width="100" />
        <el-table-column prop="free" label="可用" />
        <el-table-column prop="locked" label="冻结" />
        <el-table-column prop="total" label="合计" />
      </el-table>
      <el-empty v-if="balances.length === 0 && !loadingAccount" description="暂无余额" />
    </el-card>

    <!-- 下单 -->
    <el-card v-if="configStatus.configured && configStatus.enabled !== false" shadow="never" class="order-card">
      <template #header><span>快速下单</span></template>
      <el-form :model="orderForm" label-width="110px" style="max-width:540px">
        <el-form-item label="交易对">
          <el-input v-model="orderForm.symbol" placeholder="如 BTCUSDT" style="width:200px" />
          <el-button link style="margin-left:8px" @click="fetchPrice">查价</el-button>
          <span v-if="currentPrice" class="price-tag">≈ {{ currentPrice }} USDT</span>
        </el-form-item>
        <el-form-item label="方向">
          <el-radio-group v-model="orderForm.side">
            <el-radio-button label="BUY">买入</el-radio-button>
            <el-radio-button label="SELL">卖出</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="订单类型">
          <el-select v-model="orderForm.order_type" style="width:150px">
            <el-option label="市价单 MARKET" value="MARKET" />
            <el-option label="限价单 LIMIT" value="LIMIT" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="orderForm.order_type === 'LIMIT'" label="价格">
          <el-input-number v-model="orderForm.price" :precision="4" :step="0.01" :min="0.0001" style="width:200px" />
        </el-form-item>
        <el-form-item label="数量（基础）">
          <el-input-number v-model="orderForm.quantity" :precision="6" :step="0.001" :min="0.000001" style="width:200px" />
          <span class="form-hint" style="margin-left:6px">买入可留空，改填消费金额↓</span>
        </el-form-item>
        <el-form-item v-if="orderForm.side === 'BUY' && orderForm.order_type === 'MARKET'" label="消费金额">
          <el-input-number v-model="orderForm.quote_order_qty" :precision="2" :step="10" :min="1" style="width:200px" />
          <span class="form-hint" style="margin-left:6px">USDT</span>
        </el-form-item>
        <el-form-item>
          <el-button type="danger" :loading="placingOrder" @click="placeOrder">
            {{ orderForm.side === 'BUY' ? '买入' : '卖出' }} {{ orderForm.symbol || '---' }}
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 当前挂单 -->
    <el-card v-if="configStatus.configured" shadow="never" class="orders-card">
      <template #header>
        <div class="card-header">
          <span>当前挂单</span>
          <el-button size="small" @click="loadOpenOrders">刷新</el-button>
        </div>
      </template>
      <el-table :data="openOrders" size="small" stripe>
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column prop="side" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">{{ row.side }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="90" />
        <el-table-column prop="price" label="价格" />
        <el-table-column prop="origQty" label="数量" />
        <el-table-column prop="executedQty" label="已成交" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="danger" size="small" @click="cancelOrder(row)">撤单</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="openOrders.length === 0" description="暂无挂单" />
    </el-card>

    <!-- 历史订单 -->
    <el-card v-if="configStatus.configured" shadow="never" class="history-card">
      <template #header>
        <div class="card-header">
          <span>历史订单</span>
          <div>
            <el-input v-model="historySymbol" placeholder="BTCUSDT" size="small" style="width:120px;margin-right:8px" />
            <el-button size="small" @click="loadHistory">查询</el-button>
          </div>
        </div>
      </template>
      <el-table :data="orderHistory" size="small" stripe>
        <el-table-column prop="symbol" label="交易对" width="120" />
        <el-table-column prop="side" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">{{ row.side }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="90" />
        <el-table-column prop="price" label="价格" />
        <el-table-column prop="origQty" label="数量" />
        <el-table-column prop="executedQty" label="成交量" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'FILLED' ? 'success' : row.status === 'CANCELED' ? 'info' : 'warning'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatTime(row.time) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="orderHistory.length === 0" description="输入交易对后点击查询" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

interface ConfigStatus {
  configured: boolean
  api_key_masked?: string
  enabled?: boolean
}

const configStatus = reactive<ConfigStatus>({ configured: false })
const configForm = reactive({ api_key: '', api_secret: '', enabled: true })
const savingConfig = ref(false)
const testingConn = ref(false)

const balances = ref<any[]>([])
const loadingAccount = ref(false)

const orderForm = reactive({
  symbol: 'BTCUSDT',
  side: 'BUY' as 'BUY' | 'SELL',
  order_type: 'MARKET' as 'MARKET' | 'LIMIT',
  quantity: undefined as number | undefined,
  quote_order_qty: undefined as number | undefined,
  price: undefined as number | undefined,
})
const currentPrice = ref<string>('')
const placingOrder = ref(false)

const openOrders = ref<any[]>([])
const orderHistory = ref<any[]>([])
const historySymbol = ref('BTCUSDT')

onMounted(async () => {
  await refreshConfig()
})

async function refreshConfig() {
  try {
    const res = await axios.get('/api/live/config')
    Object.assign(configStatus, res.data?.data || { configured: false })
    if (configStatus.configured) {
      loadAccount()
      loadOpenOrders()
    }
  } catch { /* not configured */ }
}

async function saveConfig() {
  if (!configForm.api_key.trim() || !configForm.api_secret.trim()) {
    ElMessage.warning('请填写 API Key 和 API Secret')
    return
  }
  savingConfig.value = true
  try {
    await axios.post('/api/live/config', configForm)
    ElMessage.success('配置已保存')
    configForm.api_key = ''
    configForm.api_secret = ''
    await refreshConfig()
  } catch {
    ElMessage.error('保存失败')
  } finally {
    savingConfig.value = false
  }
}

async function testConnection() {
  testingConn.value = true
  try {
    const res = await axios.post('/api/live/test')
    const d = res.data?.data
    if (d?.connected) {
      ElMessage.success(`连接成功！账户类型: ${d.account_type}，可交易: ${d.can_trade ? '是' : '否'}`)
    } else {
      ElMessage.error('连接失败')
    }
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '连接失败')
  } finally {
    testingConn.value = false
  }
}

async function loadAccount() {
  loadingAccount.value = true
  try {
    const res = await axios.get('/api/live/account')
    balances.value = res.data?.data?.balances || []
  } catch {
    // silently fail
  } finally {
    loadingAccount.value = false
  }
}

async function fetchPrice() {
  if (!orderForm.symbol) return
  try {
    const res = await axios.get(`/api/live/price/${orderForm.symbol}`)
    currentPrice.value = res.data?.data?.price?.toFixed(4) || ''
  } catch {
    currentPrice.value = ''
  }
}

async function placeOrder() {
  if (!orderForm.symbol) { ElMessage.warning('请填写交易对'); return }

  const confirmMsg = `确认${orderForm.side === 'BUY' ? '买入' : '卖出'} ${orderForm.symbol}？此操作涉及真实资金！`
  try {
    await ElMessageBox.confirm(confirmMsg, '实盘下单确认', {
      confirmButtonText: '确认下单',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch { return }

  placingOrder.value = true
  try {
    const payload: any = {
      symbol: orderForm.symbol,
      side: orderForm.side,
      order_type: orderForm.order_type,
    }
    if (orderForm.quantity) payload.quantity = orderForm.quantity
    if (orderForm.quote_order_qty) payload.quote_order_qty = orderForm.quote_order_qty
    if (orderForm.order_type === 'LIMIT' && orderForm.price) payload.price = orderForm.price

    const res = await axios.post('/api/live/order', payload)
    const d = res.data?.data
    ElMessage.success(`下单成功！订单号: ${d?.orderId}，状态: ${d?.status}`)
    loadAccount()
    loadOpenOrders()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '下单失败')
  } finally {
    placingOrder.value = false
  }
}

async function cancelOrder(order: any) {
  try {
    await ElMessageBox.confirm(`确认撤销 ${order.symbol} 订单 #${order.orderId}？`, '撤单确认', {
      type: 'warning',
    })
  } catch { return }
  try {
    await axios.delete(`/api/live/order/${order.orderId}`, { params: { symbol: order.symbol } })
    ElMessage.success('撤单成功')
    loadOpenOrders()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '撤单失败')
  }
}

async function loadOpenOrders() {
  try {
    const res = await axios.get('/api/live/orders')
    openOrders.value = res.data?.data || []
  } catch { /* */ }
}

async function loadHistory() {
  if (!historySymbol.value) return
  try {
    const res = await axios.get('/api/live/history', { params: { symbol: historySymbol.value } })
    orderHistory.value = res.data?.data || []
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '查询失败')
  }
}

function formatTime(ts: number) {
  if (!ts) return ''
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.live-trading {
  padding: 20px;
  max-width: 900px;
}
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-desc { color: #909399; margin: 0; font-size: 13px; }
.config-card, .account-card, .order-card, .orders-card, .history-card { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.config-hint { font-size: 12px; color: #909399; margin-top: 8px; }
.form-hint { font-size: 12px; color: #909399; }
.price-tag { margin-left: 8px; font-weight: 600; color: #409eff; }
</style>
