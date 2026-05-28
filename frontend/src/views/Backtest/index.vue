<template>
  <div class="backtest-container">
    <div class="page-header">
      <h2>回测系统</h2>
      <p class="page-desc">基于 AI 分析历史决策评估交易策略绩效</p>
    </div>

    <!-- 参数输入卡片 -->
    <el-card class="input-card" shadow="never">
      <template #header>
        <span>回测参数</span>
      </template>
      <el-form :model="form" label-width="100px" :inline="false">
        <el-row :gutter="24">
          <el-col :span="8">
            <el-form-item label="股票代码">
              <el-input
                v-model="form.symbol"
                placeholder="如 000001,600519,BTC（逗号分隔）"
                clearable
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="评估窗口">
              <el-input-number
                v-model="form.evalWindowDays"
                :min="3"
                :max="60"
                :step="5"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="手续费率">
              <el-input-number
                v-model="form.commissionRate"
                :step="0.0001"
                :precision="4"
                :min="0"
                :max="0.01"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="24">
          <el-col :span="8">
            <el-form-item label="滑点">
              <el-input-number
                v-model="form.slippageRate"
                :step="0.0001"
                :precision="4"
                :min="0"
                :max="0.05"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item>
              <el-button type="primary" :loading="loading" @click="runBacktest">
                开始回测
              </el-button>
              <el-button @click="resetForm">重置</el-button>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
    </el-card>

    <!-- 绩效指标卡片（单标的 或 组合整体） -->
    <el-row :gutter="16" v-if="metrics" class="metrics-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">净收益率</div>
          <div class="metric-value" :class="metrics.net_return_pct >= 0 ? 'positive' : 'negative'">
            {{ metrics.net_return_pct >= 0 ? '+' : '' }}{{ metrics.net_return_pct.toFixed(2) }}%
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">夏普比率</div>
          <div class="metric-value" :class="metrics.sharpe_ratio >= 1 ? 'positive' : ''">
            {{ metrics.sharpe_ratio.toFixed(2) }}
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">最大回撤</div>
          <div class="metric-value negative">
            -{{ metrics.max_drawdown_pct.toFixed(2) }}%
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="never" class="metric-card">
          <div class="metric-label">胜率</div>
          <div class="metric-value">
            {{ (metrics.win_rate * 100).toFixed(1) }}%
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 更多指标 -->
    <el-card v-if="metrics" class="detail-card" shadow="never">
      <el-descriptions :column="3" border>
        <el-descriptions-item label="年化收益率">
          <span :class="metrics.annualized_return_pct >= 0 ? 'positive' : 'negative'">
            {{ metrics.annualized_return_pct.toFixed(2) }}%
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="总收益率">
          {{ metrics.total_return_pct.toFixed(2) }}%
        </el-descriptions-item>
        <el-descriptions-item label="盈亏比">
          {{ metrics.profit_loss_ratio.toFixed(2) }}
        </el-descriptions-item>
        <el-descriptions-item label="总交易次数">
          {{ metrics.total_trades }}
        </el-descriptions-item>
        <el-descriptions-item label="手续费（单边）">
          {{ (form.commissionRate * 100).toFixed(3) }}%
        </el-descriptions-item>
        <el-descriptions-item label="滑点（单边）">
          {{ (form.slippageRate * 100).toFixed(3) }}%
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 组合回测：各标的绩效对比 -->
    <el-card v-if="portfolioResult" class="portfolio-card" shadow="never">
      <template #header>
        <span>各标的绩效</span>
      </template>
      <el-table :data="individualRows" size="small" border>
        <el-table-column prop="symbol" label="标的" width="100" />
        <el-table-column prop="net_return_pct" label="净收益率(%)" width="120">
          <template #default="{ row }">
            <span :class="row.net_return_pct >= 0 ? 'positive' : 'negative'">
              {{ row.net_return_pct >= 0 ? '+' : '' }}{{ row.net_return_pct.toFixed(2) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="annualized_return_pct" label="年化收益率(%)" width="130">
          <template #default="{ row }">
            <span :class="row.annualized_return_pct >= 0 ? 'positive' : 'negative'">
              {{ row.annualized_return_pct.toFixed(2) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="sharpe_ratio" label="夏普比率" width="100" />
        <el-table-column prop="max_drawdown_pct" label="最大回撤(%)" width="120">
          <template #default="{ row }">
            <span class="negative">-{{ row.max_drawdown_pct.toFixed(2) }}%</span>
          </template>
        </el-table-column>
        <el-table-column prop="win_rate" label="胜率" width="80">
          <template #default="{ row }">
            {{ (row.win_rate * 100).toFixed(1) }}%
          </template>
        </el-table-column>
        <el-table-column prop="total_trades" label="交易次数" width="90" />
      </el-table>
    </el-card>

    <!-- 组合回测：相关性矩阵 -->
    <el-card v-if="portfolioResult && correlationRows.length" class="correlation-card" shadow="never">
      <template #header>
        <span>收益率相关性矩阵</span>
      </template>
      <el-table :data="correlationRows" size="small" border>
        <el-table-column prop="symbol" label="标的" width="100" />
        <el-table-column
          v-for="s in portfolioResult.symbols"
          :key="s"
          :prop="s"
          :label="s"
          width="90"
        >
          <template #default="{ row }">
            <span v-if="row[s] !== null && row[s] !== undefined" :class="correlationClass(row[s])">
              {{ row[s] }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 空状态 -->
    <el-empty
      v-if="!metrics && !loading"
      description="输入参数后点击「开始回测」查看结果"
      :image-size="120"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

interface BacktestMetrics {
  total_return_pct: number
  annualized_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  win_rate: number
  profit_loss_ratio: number
  total_trades: number
  net_return_pct: number
}

interface PortfolioResult {
  symbols: string[]
  individual: Record<string, BacktestMetrics>
  portfolio: BacktestMetrics | null
  correlation: Record<string, Record<string, number | null>>
}

const loading = ref(false)
const metrics = ref<BacktestMetrics | null>(null)
const portfolioResult = ref<PortfolioResult | null>(null)

const form = reactive({
  symbol: '',
  evalWindowDays: 20,
  commissionRate: 0.0003,
  slippageRate: 0.001,
})

// 各标的绩效行（组合回测用）
const individualRows = computed(() => {
  if (!portfolioResult.value) return []
  return Object.entries(portfolioResult.value.individual).map(([symbol, m]) => ({
    symbol,
    ...m,
  }))
})

// 相关性矩阵行
const correlationRows = computed(() => {
  if (!portfolioResult.value) return []
  const corr = portfolioResult.value.correlation
  return Object.entries(corr).map(([symbol, row]) => ({
    symbol,
    ...row,
  }))
})

function correlationClass(val: number) {
  if (val >= 0.7) return 'corr-high'
  if (val <= -0.3) return 'corr-low'
  return ''
}

async function runBacktest() {
  if (!form.symbol.trim()) {
    ElMessage.warning('请输入股票代码')
    return
  }

  const symbols = form.symbol.split(',').map((s) => s.trim().toUpperCase()).filter(Boolean)
  loading.value = true
  metrics.value = null
  portfolioResult.value = null

  try {
    if (symbols.length > 1) {
      // 多标的：调用组合回测接口
      const res = await axios.post('/api/backtest/portfolio', {
        symbols,
        eval_window_days: form.evalWindowDays,
        commission_rate: form.commissionRate,
        slippage_rate: form.slippageRate,
      })
      if (res.data?.data) {
        portfolioResult.value = res.data.data
        // 用等权重组合绩效填充顶部指标卡片
        if (res.data.data.portfolio) {
          metrics.value = res.data.data.portfolio
        }
      } else {
        ElMessage.info('暂无组合回测数据，请先进行 AI 分析')
      }
    } else {
      // 单标的：走原来的接口
      const res = await axios.post('/api/backtest/run', {
        symbol: symbols[0],
        eval_window_days: form.evalWindowDays,
        commission_rate: form.commissionRate,
        slippage_rate: form.slippageRate,
      })
      if (res.data?.data) {
        metrics.value = res.data.data
      } else {
        ElMessage.info('暂无该标的的历史回测数据，请先进行 AI 分析')
      }
    }
  } catch (err: any) {
    if (err?.response?.status === 404) {
      ElMessage.info('回测接口开发中，请关注后续版本')
    } else {
      ElMessage.error('回测请求失败，请稍后重试')
    }
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.symbol = ''
  form.evalWindowDays = 20
  form.commissionRate = 0.0003
  form.slippageRate = 0.001
  metrics.value = null
  portfolioResult.value = null
}
</script>

<style scoped>
.backtest-container {
  padding: 20px;
  max-width: 1200px;
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

.input-card {
  margin-bottom: 20px;
}

.metrics-row {
  margin-bottom: 16px;
}

.metric-card {
  text-align: center;
  padding: 8px 0;
}

.metric-label {
  color: #909399;
  font-size: 13px;
  margin-bottom: 8px;
}

.metric-value {
  font-size: 26px;
  font-weight: 700;
  color: #303133;
}

.metric-value.positive { color: #f56c6c; }
.metric-value.negative { color: #67c23a; }

.detail-card {
  margin-top: 4px;
}

.portfolio-card {
  margin-top: 16px;
}

.correlation-card {
  margin-top: 16px;
}

.positive { color: #f56c6c; }
.negative { color: #67c23a; }
.corr-high { color: #e6a23c; font-weight: 600; }
.corr-low  { color: #409eff; font-weight: 600; }
.muted     { color: #c0c4cc; }
</style>
