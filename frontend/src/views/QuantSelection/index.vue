<template>
  <div class="quant-selection">
    <div class="page-header">
      <h2>量化选股</h2>
      <p class="page-desc">基于 Microsoft Qlib + LightGBM + Alpha158 因子，对 A 股全市场打分排名，自动筛选高潜力标的。</p>
    </div>

    <!-- 状态卡 -->
    <el-card shadow="never" class="status-card">
      <template #header>
        <div class="card-header">
          <span>系统状态</span>
          <el-button size="small" :loading="loadingStatus" @click="loadStatus">刷新</el-button>
        </div>
      </template>
      <el-descriptions :column="3" size="small" border>
        <el-descriptions-item label="数据目录">{{ svcStatus.data_dir || '—' }}</el-descriptions-item>
        <el-descriptions-item label="股票数量">{{ svcStatus.symbols ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="日历天数">{{ svcStatus.calendar_days ?? '—' }}</el-descriptions-item>
        <el-descriptions-item label="最新数据">{{ svcStatus.last_date || '—' }}</el-descriptions-item>
        <el-descriptions-item label="Qlib 初始化">
          <el-tag :type="svcStatus.qlib_initialized ? 'success' : 'info'" size="small">
            {{ svcStatus.qlib_initialized ? '已初始化' : '未初始化' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="模型状态">
          <el-tag :type="svcStatus.model_fitted ? 'success' : 'warning'" size="small">
            {{ svcStatus.model_fitted ? '已训练' : '未训练' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 数据构建 -->
    <el-card shadow="never" class="build-card">
      <template #header><span>数据构建</span></template>
      <el-form :model="buildForm" label-width="100px" style="max-width:520px">
        <el-form-item label="起始日期">
          <el-date-picker v-model="buildForm.start" type="date" value-format="YYYY-MM-DD"
            placeholder="2018-01-01" style="width:180px" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="buildForm.end" type="date" value-format="YYYY-MM-DD"
            placeholder="今天" style="width:180px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="building" @click="buildData">构建数据</el-button>
          <span class="form-hint" style="margin-left:8px">后台下载 AKShare 数据并写入 Qlib 二进制文件（约 10-30 分钟）</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 模型训练 -->
    <el-card shadow="never" class="fit-card">
      <template #header><span>模型训练</span></template>
      <el-form :model="fitForm" label-width="100px" style="max-width:520px">
        <el-form-item label="训练起始">
          <el-date-picker v-model="fitForm.train_start" type="date" value-format="YYYY-MM-DD"
            placeholder="2018-01-01" style="width:180px" />
        </el-form-item>
        <el-form-item label="训练结束">
          <el-date-picker v-model="fitForm.train_end" type="date" value-format="YYYY-MM-DD"
            placeholder="2022-12-31" style="width:180px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="fitting" @click="fitModel">训练 LightGBM</el-button>
          <span class="form-hint" style="margin-left:8px">Alpha158 因子 + 均方误差损失（约 1-5 分钟）</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 选股 -->
    <el-card shadow="never" class="select-card">
      <template #header>
        <div class="card-header">
          <span>量化选股</span>
          <div>
            <el-date-picker v-model="selectDate" type="date" value-format="YYYY-MM-DD"
              placeholder="选股日期" size="small" style="width:150px;margin-right:8px" />
            <el-input-number v-model="topN" :min="5" :max="100" size="small"
              style="width:100px;margin-right:8px" placeholder="Top N" />
            <el-button size="small" type="primary" :loading="selecting" @click="runSelect">开始选股</el-button>
          </div>
        </div>
      </template>

      <el-table v-if="stockList.length > 0" :data="stockList" size="small" stripe>
        <el-table-column prop="rank" label="排名" width="70" />
        <el-table-column prop="symbol" label="代码" width="100" />
        <el-table-column prop="score" label="预测得分">
          <template #default="{ row }">
            <el-progress
              :percentage="scorePercent(row.score, minScore, maxScore)"
              :color="row.score >= 0 ? '#67c23a' : '#f56c6c'"
              :show-text="false"
              style="width:80px;display:inline-block;vertical-align:middle;margin-right:8px" />
            {{ row.score.toFixed(4) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link size="small" @click="goAnalysis(row.symbol)">AI分析</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else-if="!selecting" description='点击「开始选股」获取排名列表' />
    </el-card>

    <!-- 回测 -->
    <el-card shadow="never" class="backtest-card">
      <template #header><span>策略回测</span></template>
      <el-form :model="btForm" label-width="100px" style="max-width:520px" inline>
        <el-form-item label="回测起始">
          <el-date-picker v-model="btForm.start" type="date" value-format="YYYY-MM-DD"
            placeholder="2023-01-01" style="width:160px" />
        </el-form-item>
        <el-form-item label="回测结束">
          <el-date-picker v-model="btForm.end" type="date" value-format="YYYY-MM-DD"
            placeholder="今天" style="width:160px" />
        </el-form-item>
        <el-form-item label="持仓数量">
          <el-input-number v-model="btForm.top_n" :min="5" :max="100" style="width:120px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="backtesting" @click="runBacktest">运行回测</el-button>
        </el-form-item>
      </el-form>

      <div v-if="btResult" class="bt-result">
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="回测区间">{{ btResult.start }} ~ {{ btResult.end }}</el-descriptions-item>
          <el-descriptions-item label="持仓股数">{{ btResult.top_n }}</el-descriptions-item>
          <el-descriptions-item label="年化收益">
            <span :class="btResult.annualized_return >= 0 ? 'profit' : 'loss'">
              {{ (btResult.annualized_return * 100).toFixed(2) }}%
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="夏普比率">{{ btResult.sharpe?.toFixed(4) }}</el-descriptions-item>
          <el-descriptions-item label="最大回撤">
            <span class="loss">{{ (btResult.max_drawdown * 100).toFixed(2) }}%</span>
          </el-descriptions-item>
          <el-descriptions-item label="胜率">{{ btResult.win_rate ?? '—' }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import axios from 'axios'

const router = useRouter()

const svcStatus = reactive<Record<string, any>>({})
const loadingStatus = ref(false)

const buildForm = reactive({ start: '2018-01-01', end: '' })
const building = ref(false)

const fitForm = reactive({ train_start: '2018-01-01', train_end: '2022-12-31' })
const fitting = ref(false)

const selectDate = ref('')
const topN = ref(20)
const selecting = ref(false)
const stockList = ref<any[]>([])

const btForm = reactive({ start: '2023-01-01', end: '', top_n: 20 })
const backtesting = ref(false)
const btResult = ref<any>(null)

const minScore = computed(() => stockList.value.length ? Math.min(...stockList.value.map(s => s.score)) : 0)
const maxScore = computed(() => stockList.value.length ? Math.max(...stockList.value.map(s => s.score)) : 1)

function scorePercent(score: number, min: number, max: number) {
  if (max === min) return 50
  return Math.round(((score - min) / (max - min)) * 100)
}

onMounted(() => loadStatus())

async function loadStatus() {
  loadingStatus.value = true
  try {
    const res = await axios.get('/api/qlib/status')
    Object.assign(svcStatus, res.data?.data || {})
  } catch { /* silent */ } finally {
    loadingStatus.value = false
  }
}

async function buildData() {
  building.value = true
  try {
    const payload: any = { start: buildForm.start }
    if (buildForm.end) payload.end = buildForm.end
    await axios.post('/api/qlib/build', payload)
    ElMessage.success('数据构建已在后台启动，请查看日志')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '启动失败')
  } finally {
    building.value = false
  }
}

async function fitModel() {
  fitting.value = true
  try {
    await axios.post('/api/qlib/fit', { train_start: fitForm.train_start, train_end: fitForm.train_end })
    ElMessage.success('模型训练完成')
    await loadStatus()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '训练失败')
  } finally {
    fitting.value = false
  }
}

async function runSelect() {
  selecting.value = true
  stockList.value = []
  try {
    const payload: any = { top_n: topN.value }
    if (selectDate.value) payload.date = selectDate.value
    const res = await axios.post('/api/qlib/select', payload)
    stockList.value = res.data?.data?.stocks || []
    ElMessage.success(`选股完成，共 ${stockList.value.length} 只`)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '选股失败，请先训练模型')
  } finally {
    selecting.value = false
  }
}

async function runBacktest() {
  backtesting.value = true
  btResult.value = null
  try {
    const payload: any = { start: btForm.start, top_n: btForm.top_n }
    if (btForm.end) payload.end = btForm.end
    const res = await axios.post('/api/qlib/backtest', payload)
    btResult.value = res.data?.data
    ElMessage.success('回测完成')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '回测失败')
  } finally {
    backtesting.value = false
  }
}

function goAnalysis(symbol: string) {
  router.push({ path: '/analysis', query: { stock: symbol } })
}
</script>

<style scoped>
.quant-selection { padding: 20px; max-width: 960px; }
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-desc { color: #909399; margin: 0; font-size: 13px; }
.status-card, .build-card, .fit-card, .select-card, .backtest-card { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.form-hint { font-size: 12px; color: #909399; }
.profit { color: #f56c6c; font-weight: 600; }
.loss { color: #67c23a; font-weight: 600; }
.bt-result { margin-top: 16px; }
</style>
