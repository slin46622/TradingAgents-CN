<template>
  <div class="quant-selection">
    <div class="page-header">
      <h2>量化选股</h2>
      <p class="page-desc">基于 Microsoft Qlib + LightGBM + Alpha158 因子，对 A 股全市场打分排名，自动筛选高潜力标的。</p>
    </div>

    <!-- 浮动 AI 进度按钮 + 抽屉：Teleport 到 body 避免父级 transform 遮挡 -->
    <Teleport to="body">
      <div class="ai-process-fab" @click="showProcessDrawer = true">
        <div class="fab-indicator" :class="{ running: fittingEnsemble || discovering }"></div>
        <el-icon><Monitor /></el-icon>
        <span>AI 进度</span>
      </div>

      <!-- AI 训练/因子发现进度抽屉 -->
      <el-drawer
        v-model="showProcessDrawer"
        title="AI 运行进度"
        direction="rtl"
        size="440px"
        :modal="false"
        class="process-drawer"
      >
        <!-- ① 集成模型训练 -->
        <div class="drawer-section">
          <div class="drawer-section-title">
            <el-icon><DataAnalysis /></el-icon>
            <span>集成模型训练</span>
            <el-tag v-if="fittingEnsemble" type="warning" size="small" effect="plain">运行中</el-tag>
            <el-tag v-else-if="ensembleProgress.finished && ensembleProgress.ok" type="success" size="small" effect="plain">
              已完成 {{ ensembleProgress.current }}/{{ ensembleProgress.total }}
            </el-tag>
            <el-tag v-else-if="ensembleProgress.error" type="danger" size="small" effect="plain">异常退出</el-tag>
            <el-tag v-else-if="svcStatus.ensemble_models > 0" type="success" size="small" effect="plain">
              已有 {{ svcStatus.ensemble_models }} 个
            </el-tag>
            <el-tag v-else type="info" size="small" effect="plain">未开始</el-tag>
            <span v-if="fittingEnsemble || ensembleProgress.total > 0" class="progress-fraction">
              {{ ensembleProgress.current }}/{{ ensembleProgress.total }}
            </span>
          </div>

          <!-- 进度条 -->
          <el-progress
            v-if="fittingEnsemble || (ensembleProgress.total > 0)"
            :percentage="ensembleProgress.total > 0 ? Math.round(ensembleProgress.current / ensembleProgress.total * 100) : 0"
            :status="ensembleProgress.error ? 'exception' : (ensembleProgress.finished ? 'success' : undefined)"
            :striped="fittingEnsemble"
            :striped-flow="fittingEnsemble"
            :duration="10"
            style="margin: 8px 0 4px"
          />

          <!-- 终端日志 -->
          <div ref="ensembleLogEl" class="terminal-log">
            <div v-if="!ensembleLogs.length" class="terminal-placeholder">等待训练启动…</div>
            <div
              v-for="(line, i) in ensembleLogs"
              :key="i"
              class="terminal-line"
              :class="line.level"
            >
              <span class="terminal-ts">{{ line.ts }}</span>
              <span class="terminal-msg">{{ line.msg }}</span>
            </div>
          </div>
        </div>

        <el-divider style="margin: 12px 0" />

        <!-- ② AI 因子发现 -->
        <div class="drawer-section">
          <div class="drawer-section-title">
            <el-icon><MagicStick /></el-icon>
            <span>AI 因子发现</span>
            <el-tag v-if="discovering" type="warning" size="small" effect="plain">运行中</el-tag>
            <el-tag v-else-if="factorLibrary.length > 0" type="success" size="small" effect="plain">
              已发现 {{ factorLibrary.length }} 个
            </el-tag>
            <el-tag v-else type="info" size="small" effect="plain">未开始</el-tag>
          </div>

          <template v-if="discovering">
            <el-progress
              :percentage="discoverProgress.total_factors > 0 ? Math.round(discoverProgress.evaluated / discoverProgress.total_factors * 100) : 0"
              :striped="true" :striped-flow="true" :duration="8"
              style="margin: 8px 0 4px"
            />
            <div class="terminal-log" style="height:100px">
              <div class="terminal-line info">
                <span class="terminal-ts">进度</span>
                <span class="terminal-msg">
                  第 {{ discoverProgress.current_iter }}/{{ discoverProgress.total_iters }} 轮，
                  {{ discoverProgress.evaluated }}/{{ discoverProgress.total_factors }} 因子已评估
                  <template v-if="discoverProgress.found > 0">，✓ {{ discoverProgress.found }} 个有效</template>
                </span>
              </div>
              <div v-if="discoverProgress.phase === 'proposing'" class="terminal-line warn">
                <span class="terminal-ts">状态</span>
                <span class="terminal-msg">LLM 提案中…</span>
              </div>
              <div v-if="discoverProgress.current_expr" class="terminal-line info">
                <span class="terminal-ts">当前</span>
                <span class="terminal-msg expr">{{ discoverProgress.current_expr }}</span>
              </div>
            </div>
          </template>
          <template v-else-if="factorLibrary.length > 0">
            <div class="terminal-log" style="height:120px">
              <div v-for="f in factorLibrary.slice(0, 8)" :key="f.expr" class="terminal-line info">
                <span class="terminal-ts">IC {{ f.ic_mean?.toFixed(3) }}</span>
                <span class="terminal-msg expr">{{ f.expr?.slice(0, 60) }}</span>
              </div>
              <div v-if="factorLibrary.length > 8" class="terminal-line info">
                <span class="terminal-ts">总计</span>
                <span class="terminal-msg">共 {{ factorLibrary.length }} 个因子</span>
              </div>
            </div>
          </template>
          <div v-else class="terminal-log" style="height:60px">
            <div class="terminal-placeholder">尚未运行 AI 因子发现</div>
          </div>
        </div>
      </el-drawer>
    </Teleport>

    <!-- ① 量化选股（最高频，置顶） -->
    <el-card shadow="never" class="select-card">
      <template #header>
        <div class="card-header">
          <span>量化选股</span>
          <div>
            <el-date-picker v-model="selectDate" type="date" value-format="YYYY-MM-DD"
              placeholder="选股日期" size="small" style="width:150px;margin-right:8px" />
            <el-input-number v-model="topN" :min="5" :max="100" size="small"
              style="width:100px;margin-right:8px" placeholder="Top N" />
            <el-button v-if="fitMode === 'single'" size="small" type="primary" :loading="selecting" @click="runSelect">
              开始选股
            </el-button>
            <el-button v-else size="small" type="primary" :loading="selecting" @click="runSelectEnsemble">
              集成选股
            </el-button>
          </div>
        </div>
      </template>

      <!-- 快捷状态条 -->
      <div class="quick-status-bar">
        <el-tag :type="svcStatus.model_fitted ? 'success' : 'warning'" size="small">
          {{ svcStatus.model_fitted ? '模型就绪' : '模型未训练' }}
        </el-tag>
        <el-tag v-if="svcStatus.ensemble_models > 0" type="success" size="small">
          集成 {{ svcStatus.ensemble_models }} 个
        </el-tag>
        <el-tag :type="svcStatus.symbols > 0 ? 'success' : 'danger'" size="small">
          {{ svcStatus.symbols > 0 ? `${svcStatus.symbols} 只股票` : '无数据' }}
        </el-tag>
        <span v-if="svcStatus.last_date" class="form-hint">数据至 {{ svcStatus.last_date }}</span>
        <el-radio-group v-model="fitMode" size="small" style="margin-left:auto">
          <el-radio-button value="single">单模型</el-radio-button>
          <el-radio-button value="ensemble">集成（推荐）</el-radio-button>
        </el-radio-group>
      </div>

      <el-table v-if="stockList.length > 0" :data="stockList" size="small" stripe style="margin-top:12px">
        <el-table-column prop="rank" label="排名" width="60" />
        <el-table-column prop="symbol" label="代码" width="110" />
        <el-table-column prop="name" label="名称" width="100" />
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
        <el-table-column v-if="fitMode === 'ensemble'" prop="positive_ratio" label="多模型支持率" width="120">
          <template #default="{ row }">
            <el-progress :percentage="Math.round((row.positive_ratio ?? 0) * 100)"
              :color="(row.positive_ratio ?? 0) >= 0.7 ? '#67c23a' : '#e6a23c'"
              :show-text="false" style="width:60px;display:inline-block;vertical-align:middle;margin-right:4px" />
            {{ Math.round((row.positive_ratio ?? 0) * 100) }}%
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

    <!-- ② 策略回测 -->
    <el-card shadow="never" class="backtest-card">
      <template #header>
        <div class="card-header">
          <span>策略回测</span>
          <el-radio-group v-model="btMode" size="small">
            <el-radio-button value="basic">基础回测</el-radio-button>
            <el-radio-button value="enhanced">增强回测（手续费建模）</el-radio-button>
            <el-radio-button value="indexing">指数增强策略</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <!-- 基础回测 -->
      <template v-if="btMode === 'basic'">
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
              <span :class="btResult.annualized_return >= 0 ? 'profit' : 'loss'">{{ (btResult.annualized_return * 100).toFixed(2) }}%</span>
            </el-descriptions-item>
            <el-descriptions-item label="夏普比率">{{ btResult.sharpe?.toFixed(4) }}</el-descriptions-item>
            <el-descriptions-item label="最大回撤"><span class="loss">{{ (btResult.max_drawdown * 100).toFixed(2) }}%</span></el-descriptions-item>
            <el-descriptions-item label="胜率">{{ btResult.win_rate ?? '—' }}</el-descriptions-item>
          </el-descriptions>
        </div>
      </template>

      <!-- 增强回测（手续费/滑点建模） -->
      <template v-if="btMode === 'enhanced'">
        <el-alert type="success" :closable="false" show-icon style="margin-bottom:12px"
          title="TopkDropout + A股手续费建模（买0.05% + 卖0.15%含印花税）"
          description="使用集成模型的平均预测分数，结合真实A股交易成本，输出净收益夏普/最大回撤/日胜率。" />
        <el-form :model="btEnhForm" label-width="110px" style="max-width:580px">
          <el-form-item label="回测起始">
            <el-date-picker v-model="btEnhForm.start" type="date" value-format="YYYY-MM-DD"
              placeholder="2023-01-01" style="width:160px" />
          </el-form-item>
          <el-form-item label="回测结束">
            <el-date-picker v-model="btEnhForm.end" type="date" value-format="YYYY-MM-DD"
              placeholder="今天" style="width:160px" />
          </el-form-item>
          <el-form-item label="持仓数量">
            <el-input-number v-model="btEnhForm.top_n" :min="5" :max="100" style="width:100px" />
          </el-form-item>
          <el-form-item label="每次替换">
            <el-input-number v-model="btEnhForm.n_drop" :min="1" :max="20" style="width:100px" />
            <span class="form-hint" style="margin-left:6px">只/换仓周期</span>
          </el-form-item>
          <el-form-item label="买入费率">
            <el-input-number v-model="btEnhForm.open_cost" :precision="4" :step="0.0001" :min="0" :max="0.01" style="width:120px" />
          </el-form-item>
          <el-form-item label="卖出费率">
            <el-input-number v-model="btEnhForm.close_cost" :precision="4" :step="0.0001" :min="0" :max="0.02" style="width:120px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="backtestingEnh" @click="runBacktestEnhanced">运行增强回测</el-button>
          </el-form-item>
        </el-form>
        <div v-if="btEnhResult" class="bt-result">
          <el-descriptions :column="3" size="small" border>
            <el-descriptions-item label="回测区间">{{ btEnhResult.start }} ~ {{ btEnhResult.end }}</el-descriptions-item>
            <el-descriptions-item label="模型数量">{{ btEnhResult.models_used }} 个</el-descriptions-item>
            <el-descriptions-item label="年化收益（净）">
              <span :class="btEnhResult.annualized_return >= 0 ? 'profit' : 'loss'">
                {{ (btEnhResult.annualized_return * 100).toFixed(2) }}%
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="夏普比率">
              <span :class="btEnhResult.sharpe > 1 ? 'profit' : btEnhResult.sharpe < 0 ? 'loss' : ''">
                {{ btEnhResult.sharpe?.toFixed(4) }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="最大回撤">
              <span class="loss">{{ (btEnhResult.max_drawdown * 100).toFixed(2) }}%</span>
            </el-descriptions-item>
            <el-descriptions-item label="日胜率">{{ (btEnhResult.win_rate * 100).toFixed(1) }}%</el-descriptions-item>
          </el-descriptions>
        </div>
      </template>

      <!-- 指数增强策略 -->
      <template v-if="btMode === 'indexing'">
        <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px"
          title="EnhancedIndexingStrategy — 主动 Alpha + 被动指数跟踪"
          description="在降低跟踪误差的同时，利用模型信号获取超额收益。适合以沪深300/中证500为基准的机构策略。" />
        <el-form :model="btIdxForm" label-width="110px" style="max-width:520px">
          <el-form-item label="回测起始">
            <el-date-picker v-model="btIdxForm.start" type="date" value-format="YYYY-MM-DD"
              placeholder="2023-01-01" style="width:160px" />
          </el-form-item>
          <el-form-item label="回测结束">
            <el-date-picker v-model="btIdxForm.end" type="date" value-format="YYYY-MM-DD"
              placeholder="今天" style="width:160px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="backtestingIdx" @click="runBacktestIndexing">运行指数增强回测</el-button>
          </el-form-item>
        </el-form>
        <div v-if="btIdxResult" class="bt-result">
          <el-descriptions :column="3" size="small" border>
            <el-descriptions-item label="策略">{{ btIdxResult.strategy }}</el-descriptions-item>
            <el-descriptions-item label="年化收益">
              <span :class="btIdxResult.annualized_return >= 0 ? 'profit' : 'loss'">
                {{ (btIdxResult.annualized_return * 100).toFixed(2) }}%
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="夏普比率">{{ btIdxResult.sharpe?.toFixed(4) }}</el-descriptions-item>
            <el-descriptions-item label="最大回撤"><span class="loss">{{ (btIdxResult.max_drawdown * 100).toFixed(2) }}%</span></el-descriptions-item>
            <el-descriptions-item label="日胜率">{{ (btIdxResult.win_rate * 100)?.toFixed(1) }}%</el-descriptions-item>
            <el-descriptions-item label="模型数">{{ btIdxResult.models_used }} 个</el-descriptions-item>
          </el-descriptions>
        </div>
      </template>
    </el-card>

    <!-- ② b 模型 IC/ICIR 质量评估 -->
    <el-card shadow="never" class="ic-card collapsible-card">
      <template #header>
        <div class="card-header" @click="collapsed.ic = !collapsed.ic" style="cursor:pointer">
          <span>模型质量评估（IC / ICIR）</span>
          <div style="display:flex;align-items:center;gap:8px">
            <el-tag size="small" type="warning">评估因子有效性</el-tag>
            <el-icon :class="{ 'rotate-icon': !collapsed.ic }"><ArrowDown /></el-icon>
          </div>
        </div>
      </template>
      <div v-show="!collapsed.ic">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
          title="信息系数（IC）— 衡量模型预测与实际收益的相关性"
          description="IC > 0.02 有效；IC > 0.05 优秀。ICIR = IC均值/IC标准差，衡量信号稳定性，ICIR > 0.5 为强信号。RankIC 更稳健（对极端值不敏感）。" />
        <el-form :model="icForm" label-width="90px" style="max-width:520px;margin-bottom:8px" inline>
          <el-form-item label="评估起始">
            <el-date-picker v-model="icForm.start" type="date" value-format="YYYY-MM-DD"
              placeholder="2023-01-01" style="width:150px" />
          </el-form-item>
          <el-form-item label="评估结束">
            <el-date-picker v-model="icForm.end" type="date" value-format="YYYY-MM-DD"
              placeholder="今天" style="width:150px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="evaluatingIC" @click="runICEval">计算 IC/ICIR</el-button>
          </el-form-item>
        </el-form>

        <div v-if="icResult?.results?.length">
          <el-table :data="icResult.results" size="small" stripe border style="max-width:700px">
            <el-table-column prop="model" label="模型" min-width="110" />
            <el-table-column prop="IC" label="IC 均值" width="90">
              <template #default="{ row }">
                <span :class="row.IC > 0.02 ? 'profit' : row.IC < 0 ? 'loss' : ''">{{ row.IC?.toFixed(4) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="ICIR" label="ICIR" width="90">
              <template #default="{ row }">
                <span :class="row.ICIR > 0.5 ? 'profit' : row.ICIR > 0.2 ? '' : 'loss'">{{ row.ICIR?.toFixed(4) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="RankIC" label="RankIC" width="90">
              <template #default="{ row }">
                <span :class="row.RankIC > 0.02 ? 'profit' : ''">{{ row.RankIC?.toFixed(4) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="RankICIR" label="RankICIR" width="90">
              <template #default="{ row }">{{ row.RankICIR?.toFixed(4) }}</template>
            </el-table-column>
            <el-table-column prop="IC_win_rate" label="IC胜率" width="80">
              <template #default="{ row }">{{ (row.IC_win_rate * 100).toFixed(1) }}%</template>
            </el-table-column>
            <el-table-column prop="n_dates" label="交易日数" width="80" />
          </el-table>
          <div style="margin-top:8px;font-size:12px;color:#606266">
            最优模型（ICIR最高）：<strong>{{ icResult.best_model }}</strong>
          </div>
        </div>
      </div>
    </el-card>

    <!-- ③ AI 因子实验室 -->
    <el-card shadow="never" class="lab-card">
      <template #header>
        <div class="card-header">
          <span>AI 因子实验室</span>
          <el-tag size="small" type="warning">基于 R&D-Agent-Quant 论文</el-tag>
        </div>
      </template>

      <el-alert type="info" :closable="false" show-icon style="margin-bottom:14px"
        title="AI 自动发现 Alpha 因子（R&D Loop）"
        description="DeepSeek 提出因子假设 → 系统计算 IC（信息系数）→ 好因子保留 → 结果反馈下轮迭代（Gome 动量）。IC > 0.02 为有效因子，IC > 0.04 为优质因子。" />

      <el-form label-width="100px" style="max-width:560px;margin-bottom:4px">
        <el-form-item label="循环轮数">
          <el-input-number v-model="discoverForm.n_iter" :min="1" :max="10" style="width:100px" />
          <span class="form-hint" style="margin-left:8px">每轮 DeepSeek 提案 + IC 验证，共 n 轮迭代</span>
        </el-form-item>
        <el-form-item label="每轮因子数">
          <el-input-number v-model="discoverForm.factors_per_iter" :min="2" :max="10" style="width:100px" />
        </el-form-item>
        <el-form-item label="评估区间">
          <el-date-picker v-model="discoverForm.eval_start" type="date" value-format="YYYY-MM-DD"
            placeholder="2022-01-01" style="width:150px" />
          <span style="margin:0 6px">~</span>
          <el-date-picker v-model="discoverForm.eval_end" type="date" value-format="YYYY-MM-DD"
            placeholder="2023-12-31" style="width:150px" />
        </el-form-item>
        <el-form-item>
          <el-button type="warning" :loading="discovering" @click="startDiscover">
            启动 AI 因子发现
          </el-button>
          <el-tag v-if="!discovering && factorLibrary.length > 0" type="success" size="small" style="margin-left:8px">
            已发现 {{ factorLibrary.length }} 个有效因子
          </el-tag>
        </el-form-item>
        <div v-if="discovering" style="margin-top:4px">
          <el-progress
            :percentage="discoverProgress.total_factors > 0 ? Math.round(discoverProgress.evaluated / discoverProgress.total_factors * 100) : 0"
            striped striped-flow :duration="8" />
          <div class="form-hint" style="margin-top:4px">
            <template v-if="discoverProgress.total_iters > 0">
              第 {{ discoverProgress.current_iter }}/{{ discoverProgress.total_iters }} 轮，
              已评估 {{ discoverProgress.evaluated }}/{{ discoverProgress.total_factors }} 个因子
              <span v-if="discoverProgress.phase === 'proposing'">（DeepSeek 提案中…）</span>
              <span v-if="discoverProgress.phase === 'evaluating' && discoverProgress.current_expr">
                — {{ discoverProgress.current_expr.slice(0, 60) }}
              </span>
              <span v-if="discoverProgress.found > 0" style="color:#67c23a;margin-left:8px">
                ✓ {{ discoverProgress.found }} 个有效
              </span>
            </template>
            <template v-else>R&D 循环初始化中…（每 10 秒刷新）</template>
          </div>
        </div>
      </el-form>

      <el-table v-if="factorLibrary.length > 0" :data="factorLibrary" size="small" stripe style="margin-top:8px">
        <el-table-column prop="iteration" label="轮次" width="60" />
        <el-table-column label="因子表达式" min-width="260">
          <template #default="{ row }">
            <code style="font-size:11px;word-break:break-all">{{ row.expr }}</code>
          </template>
        </el-table-column>
        <el-table-column label="IC 均值" width="90">
          <template #default="{ row }">
            <span :style="{ color: row.ic_mean >= 0.04 ? '#67c23a' : row.ic_mean >= 0.02 ? '#e6a23c' : '#f56c6c', fontWeight: '600' }">
              {{ row.ic_mean?.toFixed(4) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="ICIR" width="80">
          <template #default="{ row }">
            <span :style="{ color: (row.icir ?? 0) >= 0.5 ? '#67c23a' : '#909399' }">
              {{ row.icir?.toFixed(3) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="天数" width="65" prop="n_days" />
        <el-table-column label="质量" width="70">
          <template #default="{ row }">
            <el-tag :type="row.ic_mean >= 0.04 ? 'success' : 'warning'" size="small">
              {{ row.ic_mean >= 0.04 ? '优质' : '有效' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-divider content-position="left" style="margin-top:20px">
        <span class="form-hint">AI 策略诊断（Gome 梯度诊断）</span>
      </el-divider>
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px"
        title="AI 诊断：深度分析策略表现的原因"
        description="先运行选股或回测，再点击诊断。AI 会分析根本原因（而不只是复述数字），并给出3条具体改进建议。" />
      <el-button :loading="diagnosing" @click="runDiagnose" :disabled="!btResult && stockList.length === 0">
        AI 诊断当前策略
      </el-button>

      <div v-if="diagnosis" style="margin-top:16px">
        <el-alert :title="'总体评价：' + (diagnosis.overall || '—')"
          :type="diagnosis.overall?.includes('优') ? 'success' : diagnosis.overall?.includes('差') ? 'error' : 'warning'"
          :closable="false" show-icon style="margin-bottom:12px" />
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="根本原因">{{ diagnosis.root_cause }}</el-descriptions-item>
          <el-descriptions-item label="优势">
            <ul style="margin:0;padding-left:16px"><li v-for="s in diagnosis.strengths">{{ s }}</li></ul>
          </el-descriptions-item>
          <el-descriptions-item label="劣势">
            <ul style="margin:0;padding-left:16px"><li v-for="w in diagnosis.weaknesses">{{ w }}</li></ul>
          </el-descriptions-item>
          <el-descriptions-item label="改进建议">
            <div v-for="r in diagnosis.recommendations" :key="r.action" style="margin-bottom:6px">
              <el-tag :type="r.priority === '高' ? 'danger' : r.priority === '中' ? 'warning' : 'info'" size="small" style="margin-right:6px">{{ r.priority }}</el-tag>
              <strong>{{ r.action }}</strong> — {{ r.reason }}
            </div>
          </el-descriptions-item>
          <el-descriptions-item v-if="diagnosis.next_factor_direction" label="下步因子方向">
            {{ diagnosis.next_factor_direction }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>

    <!-- ④ 模型训练（可折叠，默认折叠） -->
    <el-card shadow="never" class="fit-card collapsible-card">
      <template #header>
        <div class="card-header" @click="collapsed.fit = !collapsed.fit" style="cursor:pointer">
          <span>模型训练</span>
          <div style="display:flex;align-items:center;gap:8px">
            <el-radio-group v-model="fitMode" size="small" @click.stop>
              <el-radio-button value="single">单模型</el-radio-button>
              <el-radio-button value="ensemble">集成（推荐）</el-radio-button>
            </el-radio-group>
            <el-icon :class="{ 'rotate-icon': !collapsed.fit }"><ArrowDown /></el-icon>
          </div>
        </div>
      </template>
      <div v-show="!collapsed.fit">
        <el-form v-if="fitMode === 'single'" :model="fitForm" label-width="100px" style="max-width:520px">
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
            <span class="form-hint" style="margin-left:8px">单个 Alpha158 因子模型（约 1-5 分钟）</span>
          </el-form-item>
        </el-form>

        <el-form v-else :model="ensembleForm" label-width="100px" style="max-width:580px">
          <el-alert type="success" :closable="false" show-icon style="margin-bottom:12px"
            title="集成模型：16 种算法 × 最多5个滚动窗口（对标 qlib 官方 Benchmark）"
            description="LGB×3 + XGB + DoubleEnsemble + LinearRidge + GRU + LSTM + ALSTM + TCN + Transformer + TabNet + ADD + LocalFormer + SFM + DNN；1~5年滚动窗口；多数投票 + pos_ratio 过滤（约 60-240 分钟）" />
          <el-form-item label="训练截止">
            <el-date-picker v-model="ensembleForm.train_end" type="date" value-format="YYYY-MM-DD"
              placeholder="2022-12-31" style="width:180px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="fittingEnsemble" @click="fitEnsemble">训练集成模型（Alpha158）</el-button>
            <el-tag v-if="svcStatus.ensemble_models > 0 && !fittingEnsemble" type="success" size="small" style="margin-left:8px">
              已有 {{ svcStatus.ensemble_models }} 个模型
              <template v-if="svcStatus.ensemble_composition">
                （{{ Object.entries(svcStatus.ensemble_composition).map(([k,v]) => `${k}×${v}`).join(' ') }}）
              </template>
            </el-tag>
            <el-tag v-if="svcStatus.ensemble_saved" type="info" size="small" style="margin-left:4px">已持久化</el-tag>
          </el-form-item>
          <div v-if="fittingEnsemble" style="margin-top:4px">
            <el-progress
              :percentage="ensembleProgress.total > 0 ? Math.round(ensembleProgress.current / ensembleProgress.total * 100) : 0"
              :status="ensembleProgress.finished && !ensembleProgress.error ? 'success' : undefined"
              striped striped-flow :duration="10" />
            <div class="form-hint" style="margin-top:4px">
              <template v-if="ensembleProgress.total > 0">
                {{ ensembleProgress.current }}/{{ ensembleProgress.total }} 个模型完成
                <span v-if="ensembleProgress.current_label">（当前：{{ ensembleProgress.current_label }}）</span>
              </template>
              <template v-else>后台训练中，等待第一个模型完成…</template>
            </div>
          </div>

          <el-divider content-position="left" style="margin:16px 0 12px"><span class="form-hint">深度学习模型（Alpha360，需 PyTorch GPU）</span></el-divider>
          <el-form-item label="DL 训练截止">
            <el-date-picker v-model="alpha360Form.train_end" type="date" value-format="YYYY-MM-DD"
              placeholder="2022-12-31" style="width:180px" />
          </el-form-item>
          <el-form-item>
            <el-button type="warning" :loading="fittingAlpha360" @click="fitAlpha360">
              训练 Alpha360 深度学习模型
            </el-button>
            <el-tag size="small" style="margin-left:8px">GRU · LSTM · ALSTM · TCN · Transformer</el-tag>
          </el-form-item>
          <div v-if="fittingAlpha360" style="margin-top:4px">
            <el-progress
              :percentage="alpha360Progress.total > 0 ? Math.round(alpha360Progress.completed / alpha360Progress.total * 100) : 0"
              :status="alpha360Progress.status === 'completed' ? 'success' : alpha360Progress.status === 'failed' ? 'exception' : undefined"
              striped striped-flow :duration="10" />
            <div class="form-hint" style="margin-top:4px">
              {{ alpha360Progress.completed ?? 0 }}/{{ alpha360Progress.total ?? 5 }} 个 DL 模型完成
              <span v-if="alpha360Progress.current_model">（当前：{{ alpha360Progress.current_model }}）</span>
            </div>
          </div>

          <el-divider content-position="left" style="margin:16px 0 12px"><span class="form-hint">滚动重训练（用最近 N 天数据更新模型）</span></el-divider>
          <el-form-item label="训练窗口">
            <el-input-number v-model="retrainForm.days_back" :min="90" :max="1825" style="width:120px" />
            <span class="form-hint" style="margin-left:6px">天（建议365天）</span>
          </el-form-item>
          <el-form-item>
            <el-button type="info" :loading="retraining" @click="runRetrain">
              滚动重训练
            </el-button>
            <span class="form-hint" style="margin-left:8px">用最新数据替换旧模型权重（在线学习）</span>
          </el-form-item>
          <div v-if="retrainProgress.status === 'running'" class="form-hint" style="margin-top:4px">
            {{ retrainProgress.last_message ?? '训练中…' }}
          </div>
          <el-tag v-if="retrainProgress.status === 'completed'" type="success" size="small">重训练完成</el-tag>
          <el-tag v-if="retrainProgress.status === 'failed'" type="danger" size="small">重训练失败: {{ retrainProgress.error }}</el-tag>
        </el-form>
      </div>
    </el-card>

    <!-- ⑤ 数据准备（可折叠，默认折叠） -->
    <el-card shadow="never" class="build-card collapsible-card">
      <template #header>
        <div class="card-header" @click="collapsed.data = !collapsed.data" style="cursor:pointer">
          <div style="display:flex;align-items:center;gap:8px">
            <span>数据准备</span>
            <el-tag size="small" type="success">首次使用先执行</el-tag>
          </div>
          <el-icon :class="{ 'rotate-icon': !collapsed.data }"><ArrowDown /></el-icon>
        </div>
      </template>
      <div v-show="!collapsed.data">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px"
          title="推荐：一键下载预构建数据（chenditc/investment_data）"
          description="从 GitHub Releases 下载约 2 GB 的预构建 Qlib 二进制数据，无需 AKShare，约 5-15 分钟完成（取决于网速）。下载完成后刷新状态即可训练模型。" />
        <el-form label-width="100px" style="max-width:520px;margin-bottom:8px">
          <el-form-item label="发布版本">
            <el-input v-model="downloadForm.release_date" placeholder="2024-09-10" style="width:160px" clearable />
            <span class="form-hint" style="margin-left:8px">留空使用默认最新版</span>
          </el-form-item>
          <el-form-item>
            <el-button type="success" :loading="downloading" @click="downloadPrebuilt">
              一键下载预构建数据
            </el-button>
            <el-button size="small" style="margin-left:8px" @click="loadStatus" :loading="loadingStatus">
              刷新状态
            </el-button>
            <el-tag v-if="downloading" type="warning" size="small" style="margin-left:8px">后台下载中…</el-tag>
          </el-form-item>
        </el-form>

        <el-divider content-position="left"><span class="form-hint">数据已有？补充最新数据（增量更新）</span></el-divider>
        <el-alert type="warning" :closable="false" show-icon style="margin-bottom:12px"
          title="增量数据更新（填补数据缺口到今天）"
          :description="incrementalUpdateDesc" />
        <el-form label-width="100px" style="max-width:600px;margin-bottom:8px">
          <el-form-item label="回溯天数">
            <el-input-number v-model="updateForm.days_back" :min="7" :max="3650" style="width:140px" />
            <span class="form-hint" style="margin-left:8px">
              <template v-if="svcStatus.recommended_days_back">
                推荐 {{ svcStatus.recommended_days_back }} 天
                <el-button link size="small" style="margin-left:4px;font-size:12px" @click="updateForm.days_back = svcStatus.recommended_days_back">
                  使用推荐值
                </el-button>
              </template>
              <template v-else>首次建议填 600（覆盖约 2 年）</template>
            </span>
          </el-form-item>
          <el-form-item>
            <el-button type="warning" :loading="updating" @click="updateData">
              增量更新数据
            </el-button>
            <el-tag v-if="updating" type="warning" size="small" style="margin-left:8px">后台更新中…（约 2-4 小时）</el-tag>
          </el-form-item>
        </el-form>

        <el-divider content-position="left"><span class="form-hint">或者：从 AKShare 完整构建（需安装 akshare，耗时 10-30 分钟）</span></el-divider>
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
            <el-button :loading="building" @click="buildData">AKShare 构建数据</el-button>
          </el-form-item>
        </el-form>
      </div>
    </el-card>

    <!-- ⑥ 系统状态（紧凑，可折叠） -->
    <el-card shadow="never" class="status-card collapsible-card">
      <template #header>
        <div class="card-header" @click="collapsed.status = !collapsed.status" style="cursor:pointer">
          <div style="display:flex;align-items:center;gap:8px">
            <span>系统状态</span>
            <el-button size="small" :loading="loadingStatus" @click.stop="loadStatus">刷新</el-button>
          </div>
          <el-icon :class="{ 'rotate-icon': !collapsed.status }"><ArrowDown /></el-icon>
        </div>
      </template>
      <div v-show="!collapsed.status">
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="数据目录">{{ svcStatus.data_dir || '—' }}</el-descriptions-item>
          <el-descriptions-item label="股票数量">{{ svcStatus.symbols ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="日历天数">{{ svcStatus.calendar_days ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="实际数据范围">
            <span v-if="svcStatus.first_date && svcStatus.last_date">
              {{ svcStatus.first_date }} ~ {{ svcStatus.last_date }}
              <el-tag v-if="svcStatus.calendar_last_date && svcStatus.calendar_last_date !== svcStatus.last_date"
                type="info" size="small" style="margin-left:6px">
                日历至 {{ svcStatus.calendar_last_date }}
              </el-tag>
            </span>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="数据新鲜度">
            <template v-if="svcStatus.days_since_last_update != null">
              <el-tag :type="dataFreshnessType" size="small">{{ dataFreshnessLabel }}</el-tag>
              <span class="form-hint" style="margin-left:6px">{{ svcStatus.days_since_last_update }} 天前</span>
            </template>
            <span v-else>—</span>
          </el-descriptions-item>
          <el-descriptions-item label="模型状态">
            <el-tag :type="svcStatus.model_fitted ? 'success' : 'warning'" size="small">
              {{ svcStatus.model_fitted ? '已训练' : '未训练' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Qlib 初始化">
            <el-tag :type="svcStatus.qlib_initialized ? 'success' : 'info'" size="small">
              {{ svcStatus.qlib_initialized ? '已初始化' : '未初始化' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert v-if="svcStatus.symbols > 0" style="margin-top:12px"
          :type="dataAlertType" :closable="false" show-icon
          :title="dataAlertTitle"
          :description="dataAlertDesc" />
        <el-alert v-else style="margin-top:12px"
          type="warning" :closable="false" show-icon
          title="暂无本地数据"
          description="请先点击「增量更新数据」获取最近数据，或使用「一键下载预构建数据」获取历史数据（约 2GB，适合首次使用）。" />
      </div>
    </el-card>

    <!-- ⑦ 自动定时运行（可折叠，默认折叠） -->
    <el-card shadow="never" class="nightly-card collapsible-card">
      <template #header>
        <div class="card-header" @click="collapsed.nightly = !collapsed.nightly" style="cursor:pointer">
          <div style="display:flex;align-items:center;gap:8px">
            <span>自动定时运行</span>
            <el-tag size="small" type="info">每天收盘后自动选股 / 定时自动探索因子</el-tag>
          </div>
          <el-icon :class="{ 'rotate-icon': !collapsed.nightly }"><ArrowDown /></el-icon>
        </div>
      </template>
      <div v-show="!collapsed.nightly">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px"
          title="自动运行说明"
          description="开启后，系统将按设定时间自动运行：① 集成选股（需先训练模型）② 自动因子发现（需要 Qlib 数据）。结果保存在下方「上次自动选股结果」。修改配置后点击保存即可生效，无需重启。" />

        <div style="margin-bottom:16px">
          <div style="font-weight:600;margin-bottom:8px">
            <el-switch v-model="nightlyConfig.select_enabled" style="margin-right:8px" />
            自动集成选股
            <el-tag v-if="nightlyConfig.next_select_run" size="small" type="success" style="margin-left:8px">
              下次运行：{{ nightlyConfig.next_select_run?.slice(0, 16).replace('T', ' ') }}
            </el-tag>
            <el-tag v-else-if="!nightlyConfig.select_enabled" size="small" type="info" style="margin-left:8px">已暂停</el-tag>
          </div>
          <el-form label-width="100px" style="max-width:560px" :disabled="!nightlyConfig.select_enabled">
            <el-form-item label="Cron 表达式">
              <el-input v-model="nightlyConfig.select_cron" style="width:180px" placeholder="0 17 * * 1-5" />
              <span class="form-hint" style="margin-left:8px">默认：工作日 17:00（收盘后）</span>
            </el-form-item>
            <el-form-item label="选股数量">
              <el-input-number v-model="nightlyConfig.select_top_n" :min="5" :max="200" style="width:120px" />
            </el-form-item>
            <el-form-item label="多模型支持率">
              <el-slider v-model="nightlyConfig.select_min_positive_ratio" :min="0" :max="1" :step="0.1"
                :marks="{0:'0%',0.5:'50%',1:'100%'}" style="width:220px" />
            </el-form-item>
          </el-form>
        </div>

        <el-divider content-position="left"><span class="form-hint">定时自动因子发现（支持每日/每小时）</span></el-divider>

        <div style="margin-bottom:16px">
          <div style="font-weight:600;margin-bottom:8px">
            <el-switch v-model="nightlyConfig.discover_enabled" style="margin-right:8px" />
            自动因子发现
            <el-tag v-if="nightlyConfig.next_discover_run" size="small" type="success" style="margin-left:8px">
              下次运行：{{ nightlyConfig.next_discover_run?.slice(0, 16).replace('T', ' ') }}
            </el-tag>
            <el-tag v-else-if="!nightlyConfig.discover_enabled" size="small" type="info" style="margin-left:8px">已暂停</el-tag>
          </div>
          <el-form label-width="100px" style="max-width:560px" :disabled="!nightlyConfig.discover_enabled">
            <el-form-item label="Cron 表达式">
              <el-input v-model="nightlyConfig.discover_cron" style="width:180px" placeholder="0 20 * * 1-5" />
              <span class="form-hint" style="margin-left:8px">工作日 20:00。隔夜挖掘可设 <code>0 22 * * *</code>（每天22点）</span>
            </el-form-item>
            <el-form-item label="迭代轮数">
              <el-input-number v-model="nightlyConfig.discover_n_iter" :min="1" :max="10" style="width:100px" />
            </el-form-item>
            <el-form-item label="每轮因子数">
              <el-input-number v-model="nightlyConfig.discover_factors_per_iter" :min="2" :max="10" style="width:100px" />
            </el-form-item>
          </el-form>
        </div>

        <div style="margin-bottom:20px">
          <el-button type="primary" :loading="savingNightly" @click="saveNightlyConfig">保存配置</el-button>
          <el-button :loading="triggeringNightly" @click="triggerNightlyNow" style="margin-left:8px">
            立即运行一次选股
          </el-button>
          <el-button link size="small" style="margin-left:8px" @click="loadNightlyResult">刷新结果</el-button>
        </div>

        <el-divider content-position="left"><span class="form-hint">上次自动选股结果</span></el-divider>
        <div v-if="nightlyResult.ran_at" class="form-hint" style="margin-bottom:8px">
          运行时间：{{ nightlyResult.ran_at?.slice(0, 19).replace('T', ' ') }}
          &nbsp;|&nbsp; 共 {{ nightlyResult.total }} 只
          <span v-if="nightlyResult.models_used"> &nbsp;|&nbsp; {{ nightlyResult.models_used }} 个模型</span>
        </div>
        <el-table v-if="nightlyResult.stocks?.length > 0" :data="nightlyResult.stocks" size="small" stripe style="max-height:300px;overflow-y:auto">
          <el-table-column prop="rank" label="排名" width="60" />
          <el-table-column prop="symbol" label="代码" width="110" />
          <el-table-column prop="name" label="名称" width="100" />
          <el-table-column label="预测得分" prop="score">
            <template #default="{ row }">
              <span :style="{ color: row.score >= 0 ? '#f56c6c' : '#67c23a', fontWeight: '600' }">
                {{ row.score?.toFixed(4) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column v-if="nightlyResult.stocks[0]?.positive_ratio != null" prop="positive_ratio" label="多模型支持" width="100">
            <template #default="{ row }">{{ Math.round((row.positive_ratio ?? 0) * 100) }}%</template>
          </el-table-column>
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link size="small" @click="goAnalysis(row.symbol)">AI分析</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无自动选股结果（开启功能后每日自动生成，或点击「立即运行一次」）" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import axios from 'axios'
import { Monitor, DataAnalysis, MagicStick, ArrowDown } from '@element-plus/icons-vue'

const router = useRouter()

const svcStatus = reactive<Record<string, any>>({})
const loadingStatus = ref(false)

const downloadForm = reactive({ release_date: '' })
const downloading = ref(false)

const updateForm = reactive({ days_back: 600 })
const updating = ref(false)

const buildForm = reactive({ start: '2018-01-01', end: '' })
const building = ref(false)

const fitMode = ref<'single' | 'ensemble'>('ensemble')
const fitForm = reactive({ train_start: '2024-10-08', train_end: '2025-10-31' })
const fitting = ref(false)
const ensembleForm = reactive({ train_end: '2025-10-31' })
const fittingEnsemble = ref(false)

const selectDate = ref('')
const topN = ref(20)
const selecting = ref(false)
const stockList = ref<any[]>([])

const btForm = reactive({ start: '2023-01-01', end: '', top_n: 20 })
const backtesting = ref(false)
const btResult = ref<any>(null)

// AI Factor Lab
const discoverForm = reactive({ n_iter: 3, factors_per_iter: 5, eval_start: '2022-01-01', eval_end: '2023-12-31' })
const discovering = ref(false)
const factorLibrary = ref<any[]>([])
const diagnosing = ref(false)
const diagnosis = ref<any>(null)
const discoverProgress = ref<any>({})

// Ensemble fit progress
const ensembleProgress = ref<any>({})
const ensembleLogs = ref<Array<{ts: string; level: string; msg: string}>>([])
const ensembleLogEl = ref<HTMLElement | null>(null)
// 用 timeout ID（number）取代 setInterval，避免请求堆积
let ensemblePollTimeout: ReturnType<typeof setTimeout> | null = null
let ensemblePollActive = false
let ensemblePollAbort: AbortController | null = null
let discoverPollTimeout: ReturnType<typeof setTimeout> | null = null
let discoverPollActive = false
let discoverPollAbort: AbortController | null = null

// Nightly auto-run
const nightlyConfig = reactive<any>({
  select_enabled: false,
  select_cron: '0 17 * * 1-5',
  select_top_n: 20,
  select_min_positive_ratio: 0.5,
  discover_enabled: false,
  discover_cron: '0 20 * * 5',
  discover_n_iter: 2,
  discover_factors_per_iter: 5,
  next_select_run: null,
  next_discover_run: null,
})
const nightlyResult = ref<any>({ stocks: [], total: 0, ran_at: null })
const savingNightly = ref(false)
const triggeringNightly = ref(false)

// UI state
const showProcessDrawer = ref(false)
const collapsed = reactive({
  fit: true,
  data: true,
  status: true,
  nightly: true,
  ic: true,
})

// IC/ICIR evaluation
const icForm = reactive({ start: '2023-01-01', end: '' })
const icResult = ref<any>(null)
const evaluatingIC = ref(false)

// Enhanced backtest
const btMode = ref('basic')
const btEnhForm = reactive({ start: '2023-01-01', end: '', top_n: 20, n_drop: 5, open_cost: 0.0005, close_cost: 0.0015 })
const btEnhResult = ref<any>(null)
const backtestingEnh = ref(false)
const btIdxForm = reactive({ start: '2023-01-01', end: '' })
const btIdxResult = ref<any>(null)
const backtestingIdx = ref(false)

// Alpha360 DL training
const alpha360Form = reactive({ train_end: '2022-12-31' })
const fittingAlpha360 = ref(false)
const alpha360Progress = ref<any>({})
let alpha360PollTimeout: ReturnType<typeof setTimeout> | null = null
let alpha360PollActive = false

// Rolling retrain
const retrainForm = reactive({ days_back: 365 })
const retraining = ref(false)
const retrainProgress = ref<any>({})

const minScore = computed(() => stockList.value.length ? Math.min(...stockList.value.map(s => s.score)) : 0)
const maxScore = computed(() => stockList.value.length ? Math.max(...stockList.value.map(s => s.score)) : 1)

const dataFreshnessType = computed(() => {
  const d = svcStatus.days_since_last_update
  if (d == null) return 'info'
  if (d <= 3) return 'success'
  if (d <= 14) return 'warning'
  return 'danger'
})
const dataFreshnessLabel = computed(() => {
  const d = svcStatus.days_since_last_update
  if (d == null) return '未知'
  if (d <= 3) return '最新'
  if (d <= 14) return '较新'
  if (d <= 60) return '需更新'
  return '严重滞后'
})
const dataAlertType = computed(() => {
  const d = svcStatus.days_since_last_update
  if (d == null || d > 14) return 'warning'
  return 'success'
})
const dataAlertTitle = computed(() => {
  const d = svcStatus.days_since_last_update
  if (d == null) return '数据状态未知'
  if (d <= 3) return `✅ 数据已是最新（最新至 ${svcStatus.last_date}）`
  if (d <= 14) return `⚠️ 数据距今 ${d} 天，建议增量更新`
  return `❗ 数据已 ${d} 天未更新，建议尽快增量更新`
})
const dataAlertDesc = computed(() => {
  const d = svcStatus.days_since_last_update
  const sym = svcStatus.symbols ?? 0
  const range = svcStatus.first_date && svcStatus.last_date
    ? `数据范围 ${svcStatus.first_date} ~ ${svcStatus.last_date}，共 ${svcStatus.calendar_days} 个交易日，${sym} 支股票。`
    : `共 ${sym} 支股票。`
  if (d == null || d <= 3) return `${range}无需重复下载预构建数据。下次增量更新填 ${svcStatus.recommended_days_back ?? 30} 天即可。`
  return `${range}建议点击「增量更新数据」，回溯天数填 ${svcStatus.recommended_days_back ?? d + 7} 即可覆盖缺口。`
})
const incrementalUpdateDesc = computed(() => {
  const d = svcStatus.days_since_last_update
  if (d != null && d <= 3) {
    return `当前数据已是最新（${svcStatus.last_date}），无需更新。若需补充更多历史数据可手动调整天数。`
  }
  if (d != null) {
    return `本地数据最新至 ${svcStatus.last_date}，距今 ${d} 天。建议回溯天数填 ${svcStatus.recommended_days_back ?? d + 7}，通过 AKShare 补充缺口至今天。约需 2-4 小时（后台运行）。`
  }
  return '通过 AKShare 补充本地数据至今天。约需 2-4 小时（后台运行），不影响现有数据。首次使用建议填 600 天（覆盖约 2 年）。'
})

function scorePercent(score: number, min: number, max: number) {
  if (max === min) return 50
  return Math.round(((score - min) / (max - min)) * 100)
}

onMounted(async () => {
  loadStatus()
  loadNightlyConfig()
  loadNightlyResult()
  // 自动恢复上次选股结果
  try {
    const lastRes = await axios.get('/api/qlib/select/last')
    const lastData = lastRes.data?.data
    if (lastData?.stocks?.length > 0 && stockList.value.length === 0) {
      stockList.value = lastData.stocks
      fitMode.value = 'ensemble'
      ElMessage.info(`已加载上次选股结果（${lastData.date}，共 ${lastData.stocks.length} 只）`)
    }
  } catch { /* 静默 */ }
  try {
    const [fitRes, discRes] = await Promise.all([
      axios.get('/api/qlib/fit/ensemble/status'),
      axios.get('/api/qlib/discover/status'),
    ])
    const fitData = fitRes.data?.data
    if (fitData?.running) {
      fittingEnsemble.value = true
      ensembleProgress.value = fitData.progress || {}
      _syncEnsembleLogs(fitData.progress?.logs || [])
      _startEnsemblePolling()
      ElMessage.info('集成训练正在后台运行，进度已恢复')
    }
    const discData = discRes.data?.data
    if (discData?.running) {
      discovering.value = true
      discoverProgress.value = discData.progress || {}
      if (discData.library?.length) factorLibrary.value = discData.library
      _startDiscoverPolling()
      ElMessage.info('AI 因子发现正在后台运行，进度已恢复')
    }
  } catch { /* 静默失败，不影响页面正常加载 */ }
})

onUnmounted(() => {
  _stopEnsemblePolling()
  _stopDiscoverPolling()
})

async function loadStatus() {
  loadingStatus.value = true
  try {
    const res = await axios.get('/api/qlib/status')
    Object.assign(svcStatus, res.data?.data || {})
    if (svcStatus.recommended_days_back && svcStatus.recommended_days_back !== 600) {
      updateForm.days_back = svcStatus.recommended_days_back
    }
    if (svcStatus.first_date) {
      fitForm.train_start = svcStatus.first_date
    }
    if (svcStatus.last_date) {
      const lastDt = new Date(svcStatus.last_date)
      lastDt.setMonth(lastDt.getMonth() - 6)
      const trainEnd = lastDt.toISOString().split('T')[0]
      fitForm.train_end = trainEnd
      ensembleForm.train_end = trainEnd
    }
  } catch { /* silent */ } finally {
    loadingStatus.value = false
  }
}

async function downloadPrebuilt() {
  downloading.value = true
  try {
    const payload: any = {}
    if (downloadForm.release_date) payload.release_date = downloadForm.release_date
    await axios.post('/api/qlib/download', payload)
    ElMessage.success('预构建数据下载已在后台启动，约 5-15 分钟后刷新状态')
    const poll = setInterval(async () => {
      const r = await axios.get('/api/qlib/download/status')
      if (!r.data?.data?.running) {
        clearInterval(poll)
        downloading.value = false
        await loadStatus()
        ElMessage.success('数据下载完成，可以开始训练模型')
      }
    }, 30000)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '启动下载失败')
    downloading.value = false
  }
}

async function updateData() {
  updating.value = true
  try {
    await axios.post('/api/qlib/update', { days_back: updateForm.days_back })
    ElMessage.success(`增量数据更新已在后台启动（最近 ${updateForm.days_back} 天），约 2-4 小时完成后刷新状态`)
    const poll = setInterval(async () => {
      const r = await axios.get('/api/qlib/update/status')
      if (!r.data?.data?.running) {
        clearInterval(poll)
        updating.value = false
        await loadStatus()
        ElMessage.success('增量数据更新完成，最新数据已就绪')
      }
    }, 60000)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '启动增量更新失败')
    updating.value = false
  }
}

async function buildData() {
  building.value = true
  try {
    const payload: any = { start: buildForm.start }
    if (buildForm.end) payload.end = buildForm.end
    await axios.post('/api/qlib/build', payload)
    ElMessage.success('数据构建已在后台启动，请查看服务器日志（需已安装 akshare）')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '启动失败')
  } finally {
    building.value = false
  }
}

async function fitModel() {
  fitting.value = true
  try {
    if (svcStatus.symbols === 0) {
      ElMessage.warning('请先完成数据准备（下载预构建数据或 AKShare 构建），再训练模型')
      return
    }
    await axios.post('/api/qlib/fit', { train_start: fitForm.train_start, train_end: fitForm.train_end })
    ElMessage.success('模型训练完成')
    await loadStatus()
  } catch (err: any) {
    const detail = err?.response?.data?.detail || '训练失败'
    ElMessage.error(detail === 'Qlib not initialized'
      ? '请先完成数据准备步骤（下载预构建数据），再训练模型'
      : detail)
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
    const detail = err?.response?.data?.detail || ''
    ElMessage.error(detail.includes('fitted') || detail.includes('fit')
      ? '请先训练模型：完成数据准备后，在"模型训练"中点击"训练 LightGBM"'
      : detail || '选股失败，请先训练模型')
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

async function runICEval() {
  evaluatingIC.value = true
  icResult.value = null
  try {
    const payload: any = { start: icForm.start }
    if (icForm.end) payload.end = icForm.end
    const res = await axios.post('/api/qlib/evaluate/ic', payload)
    icResult.value = res.data?.data
    ElMessage.success(`IC评估完成，最优模型: ${icResult.value?.best_model ?? '—'}`)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || 'IC 评估失败，请先训练集成模型')
  } finally {
    evaluatingIC.value = false
  }
}

async function runBacktestEnhanced() {
  backtestingEnh.value = true
  btEnhResult.value = null
  try {
    const payload: any = { ...btEnhForm }
    if (!payload.end) delete payload.end
    const res = await axios.post('/api/qlib/backtest/enhanced', payload)
    btEnhResult.value = res.data?.data
    ElMessage.success('增强回测完成')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '增强回测失败')
  } finally {
    backtestingEnh.value = false
  }
}

async function runBacktestIndexing() {
  backtestingIdx.value = true
  btIdxResult.value = null
  try {
    const payload: any = { ...btIdxForm }
    if (!payload.end) delete payload.end
    const res = await axios.post('/api/qlib/backtest/enhanced-indexing', payload)
    btIdxResult.value = res.data?.data
    ElMessage.success('指数增强回测完成')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '指数增强回测失败')
  } finally {
    backtestingIdx.value = false
  }
}

async function fitAlpha360() {
  fittingAlpha360.value = true
  alpha360Progress.value = {}
  try {
    await axios.post('/api/qlib/fit/alpha360', { train_end: alpha360Form.train_end })
    ElMessage.success('Alpha360 深度学习训练已在后台启动')
    _startAlpha360Polling()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || 'Alpha360 训练启动失败')
    fittingAlpha360.value = false
  }
}

function _startAlpha360Polling() {
  if (alpha360PollActive) return
  alpha360PollActive = true
  async function _tick() {
    if (!alpha360PollActive) return
    try {
      const r = await axios.get('/api/qlib/fit/alpha360/status', { timeout: 8000 })
      const d = r.data?.data
      alpha360Progress.value = d?.progress || {}
      if (!d?.running) {
        alpha360PollActive = false
        if (alpha360PollTimeout) { clearTimeout(alpha360PollTimeout); alpha360PollTimeout = null }
        fittingAlpha360.value = false
        const prog = d?.progress || {}
        if (prog.status === 'completed') {
          ElMessage.success(`Alpha360 训练完成，${prog.result?.models ?? 0} 个DL模型已并入集成`)
          await loadStatus()
        } else if (prog.status === 'failed') {
          ElMessage.error(`Alpha360 训练失败: ${prog.error ?? ''}`)
        }
        return
      }
    } catch { /* silent */ }
    if (alpha360PollActive) alpha360PollTimeout = setTimeout(_tick, 10000)
  }
  _tick()
}

async function runRetrain() {
  retraining.value = true
  retrainProgress.value = { status: 'running' }
  try {
    await axios.post('/api/qlib/retrain', { days_back: retrainForm.days_back })
    ElMessage.success(`滚动重训练已在后台启动（最近 ${retrainForm.days_back} 天）`)
    const poll = setInterval(async () => {
      try {
        const r = await axios.get('/api/qlib/retrain/status')
        const d = r.data?.data
        retrainProgress.value = d?.progress || {}
        if (!d?.running) {
          clearInterval(poll)
          retraining.value = false
          if (d?.progress?.status === 'completed') {
            ElMessage.success('滚动重训练完成，模型已更新')
            await loadStatus()
          }
        }
      } catch { /* silent */ }
    }, 15000)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '滚动重训练启动失败')
    retraining.value = false
    retrainProgress.value = {}
  }
}

const MAX_LOG_LINES = 300  // 超过时保留最新 200 行，避免 DOM 无限增长

function _syncEnsembleLogs(logs: any[]) {
  if (!Array.isArray(logs) || logs.length === 0) return
  const current = ensembleLogs.value.length
  if (logs.length <= current) return           // 无新行，跳过

  // 只追加新增的行，不替换整个数组
  const newLines = logs.slice(current)
  ensembleLogs.value.push(...newLines)

  // 超限时裁剪旧行（保留最新 200 行），减少 DOM 节点数
  if (ensembleLogs.value.length > MAX_LOG_LINES) {
    ensembleLogs.value = ensembleLogs.value.slice(-200)
  }

  // 自动滚到底部
  nextTick(() => {
    if (ensembleLogEl.value) {
      ensembleLogEl.value.scrollTop = ensembleLogEl.value.scrollHeight
    }
  })
}

function _stopEnsemblePolling() {
  ensemblePollActive = false
  if (ensemblePollTimeout) { clearTimeout(ensemblePollTimeout); ensemblePollTimeout = null }
  if (ensemblePollAbort) { ensemblePollAbort.abort(); ensemblePollAbort = null }
}

function _startEnsemblePolling() {
  if (ensemblePollActive) return
  ensemblePollActive = true

  async function _tick() {
    if (!ensemblePollActive) return
    ensemblePollAbort = new AbortController()
    try {
      const r = await axios.get('/api/qlib/fit/ensemble/status', {
        signal: ensemblePollAbort.signal,
        timeout: 8000,   // 请求最多等 8s，超时直接跳过本轮
      })
      const d = r.data?.data
      const prog = d?.progress || {}
      ensembleProgress.value = prog
      _syncEnsembleLogs(prog.logs || [])
      if (!d?.running) {
        _stopEnsemblePolling()
        fittingEnsemble.value = false
        if (prog.error) {
          ElMessage.error(`集成训练失败: ${prog.error}`)
        } else if (prog.finished) {
          ElMessage.success(`集成模型训练完成，共 ${prog.current} 个模型`)
          await loadStatus()
        }
        return
      }
    } catch (e: any) {
      if (axios.isCancel?.(e) || e?.name === 'CanceledError') return  // 被主动取消，不重试
    } finally {
      ensemblePollAbort = null
    }
    // 上一轮完成后才安排下一轮，间隔 5s
    if (ensemblePollActive) {
      ensemblePollTimeout = setTimeout(_tick, 5000)
    }
  }
  _tick()
}

async function fitEnsemble() {
  if (svcStatus.symbols === 0) {
    ElMessage.warning('请先完成数据准备（下载预构建数据），再训练模型')
    return
  }
  _stopEnsemblePolling()
  fittingEnsemble.value = true
  ensembleProgress.value = {}
  ensembleLogs.value = []
  try {
    await axios.post('/api/qlib/fit/ensemble', { train_end: ensembleForm.train_end })
    ElMessage.success('集成训练已在后台启动，点击右下角"AI进度"按钮查看实时日志')
    _startEnsemblePolling()
  } catch (err: any) {
    const detail = err?.response?.data?.detail || '集成训练启动失败'
    ElMessage.error(detail === 'Qlib not initialized'
      ? '请先完成数据准备步骤（下载预构建数据），再训练模型'
      : detail)
    fittingEnsemble.value = false
  }
}

async function runSelectEnsemble() {
  selecting.value = true
  stockList.value = []
  try {
    const payload: any = { top_n: topN.value }
    if (selectDate.value) payload.date = selectDate.value
    const res = await axios.post('/api/qlib/select/ensemble', payload)
    const data = res.data?.data
    stockList.value = data?.stocks || []
    const modelsUsed = data?.models_used ?? 0
    ElMessage.success(`集成选股完成，共 ${stockList.value.length} 只（使用 ${modelsUsed} 个模型）`)
  } catch (err: any) {
    const detail = err?.response?.data?.detail || ''
    ElMessage.error(detail.includes('fitted') || detail.includes('Ensemble')
      ? '请先训练集成模型：在"模型训练"选择"集成模型"后点击"训练集成模型"'
      : detail || '集成选股失败，请先训练集成模型')
  } finally {
    selecting.value = false
  }
}

function _stopDiscoverPolling() {
  discoverPollActive = false
  if (discoverPollTimeout) { clearTimeout(discoverPollTimeout); discoverPollTimeout = null }
  if (discoverPollAbort) { discoverPollAbort.abort(); discoverPollAbort = null }
}

function _startDiscoverPolling() {
  if (discoverPollActive) return
  discoverPollActive = true

  async function _tick() {
    if (!discoverPollActive) return
    discoverPollAbort = new AbortController()
    try {
      const r = await axios.get('/api/qlib/discover/status', {
        signal: discoverPollAbort.signal,
        timeout: 8000,
      })
      const d = r.data?.data
      if (d?.library?.length) factorLibrary.value = d.library
      if (d?.progress) discoverProgress.value = d.progress
      if (!d?.running) {
        _stopDiscoverPolling()
        discovering.value = false
        factorLibrary.value = d?.library || []
        discoverProgress.value = {}
        ElMessage.success(`因子发现完成，共发现 ${factorLibrary.value.length} 个有效因子`)
        return
      }
    } catch (e: any) {
      if (axios.isCancel?.(e) || e?.name === 'CanceledError') return
    } finally {
      discoverPollAbort = null
    }
    if (discoverPollActive) {
      discoverPollTimeout = setTimeout(_tick, 10_000)
    }
  }
  _tick()
}

async function startDiscover() {
  _stopDiscoverPolling()
  discovering.value = true
  factorLibrary.value = []
  diagnosis.value = null
  discoverProgress.value = {}
  try {
    await axios.post('/api/qlib/discover', {
      n_iter: discoverForm.n_iter,
      factors_per_iter: discoverForm.factors_per_iter,
      eval_start: discoverForm.eval_start,
      eval_end: discoverForm.eval_end,
    })
    const total = discoverForm.n_iter * discoverForm.factors_per_iter
    ElMessage.success(`AI 因子发现已启动（共 ${total} 个因子待评估）`)
    _startDiscoverPolling()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '启动失败')
    discovering.value = false
  }
}

async function runDiagnose() {
  diagnosing.value = true
  diagnosis.value = null
  try {
    const payload: any = {}
    if (btResult.value) payload.backtest_result = btResult.value
    if (stockList.value.length > 0) payload.selection_result = { stocks: stockList.value, total: stockList.value.length }
    const res = await axios.post('/api/qlib/diagnose', payload)
    diagnosis.value = res.data?.data?.diagnosis || null
    ElMessage.success('AI 诊断完成')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || 'AI 诊断失败')
  } finally {
    diagnosing.value = false
  }
}

async function loadNightlyConfig() {
  try {
    const r = await axios.get('/api/qlib/nightly/config')
    Object.assign(nightlyConfig, r.data?.data || {})
  } catch { /* silent */ }
}

async function loadNightlyResult() {
  try {
    const r = await axios.get('/api/qlib/nightly/result')
    nightlyResult.value = r.data?.data || { stocks: [], total: 0, ran_at: null }
  } catch { /* silent */ }
}

async function saveNightlyConfig() {
  savingNightly.value = true
  try {
    await axios.post('/api/qlib/nightly/config', {
      select_enabled: nightlyConfig.select_enabled,
      select_cron: nightlyConfig.select_cron,
      select_top_n: nightlyConfig.select_top_n,
      select_min_positive_ratio: nightlyConfig.select_min_positive_ratio,
      discover_enabled: nightlyConfig.discover_enabled,
      discover_cron: nightlyConfig.discover_cron,
      discover_n_iter: nightlyConfig.discover_n_iter,
      discover_factors_per_iter: nightlyConfig.discover_factors_per_iter,
    })
    ElMessage.success('自动运行配置已保存')
    await loadNightlyConfig()
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '保存失败')
  } finally {
    savingNightly.value = false
  }
}

async function triggerNightlyNow() {
  triggeringNightly.value = true
  try {
    await axios.post('/api/qlib/nightly/run')
    ElMessage.success('夜间选股已触发，稍后刷新结果')
    setTimeout(loadNightlyResult, 5000)
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.detail || '触发失败，请确认集成模型已训练')
  } finally {
    triggeringNightly.value = false
  }
}

function goAnalysis(symbol: string) {
  router.push({ path: '/analysis', query: { stock: symbol } })
}
</script>

<style scoped>
.quant-selection { padding: 20px; max-width: 960px; position: relative; }
.page-header { margin-bottom: 20px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-desc { color: #909399; margin: 0; font-size: 13px; }
.status-card, .build-card, .fit-card, .select-card, .backtest-card, .lab-card, .nightly-card { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.form-hint { font-size: 12px; color: #909399; }
.profit { color: #f56c6c; font-weight: 600; }
.loss { color: #67c23a; font-weight: 600; }
.bt-result { margin-top: 16px; }

/* 快捷状态条 */
.quick-status-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

/* 浮动 AI 进度按钮 */
.ai-process-fab {
  position: fixed;
  right: 24px;
  bottom: 80px;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--el-color-primary);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0,0,0,0.25);
  font-size: 11px;
  gap: 2px;
  transition: transform 0.2s, box-shadow 0.2s;
  user-select: none;
}
.ai-process-fab:hover {
  transform: scale(1.08);
  box-shadow: 0 6px 20px rgba(0,0,0,0.3);
}
.ai-process-fab .el-icon { font-size: 20px; }
.fab-indicator {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #909399;
  border: 2px solid #fff;
}
.fab-indicator.running {
  background: #f56c6c;
  animation: pulse 1.2s infinite;
}
@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.3); opacity: 0.7; }
}

/* 可折叠卡片 */
.collapsible-card :deep(.el-card__header) {
  padding: 12px 16px;
}
.rotate-icon {
  transform: rotate(180deg);
  transition: transform 0.25s;
}
.el-icon {
  transition: transform 0.25s;
}

/* 抽屉内容 */
.drawer-section { padding: 0 4px 16px; }
.drawer-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 8px;
  font-size: 14px;
}
.progress-fraction {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
  font-variant-numeric: tabular-nums;
}

/* 终端日志窗口 */
.terminal-log {
  background: #1a1a2e;
  border: 1px solid #2d2d4e;
  border-radius: 6px;
  padding: 8px 10px;
  height: 220px;
  overflow-y: auto;
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 11.5px;
  line-height: 1.7;
  scroll-behavior: smooth;
}
.terminal-placeholder {
  color: #555577;
  font-style: italic;
}
.terminal-line {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  word-break: break-all;
}
.terminal-ts {
  color: #5f9ea0;
  white-space: nowrap;
  flex-shrink: 0;
  min-width: 56px;
}
.terminal-msg {
  color: #c8d3e6;
}
.terminal-line.warn .terminal-msg { color: #e8a838; }
.terminal-line.error .terminal-msg { color: #e05555; }
.terminal-msg.expr { color: #7ec8e3; font-style: italic; }

/* 旧样式保留（兼容因子发现区） */
.drawer-log {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.8;
}
.log-line { color: var(--el-text-color-regular); }
.log-line.expr { font-family: monospace; color: var(--el-color-primary); word-break: break-all; }
.log-line.found { color: #67c23a; font-weight: 600; }
</style>
