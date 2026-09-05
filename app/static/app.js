const { createApp, ref, reactive, computed, onMounted, onUnmounted, watch } = Vue;

// ---------------------------------------------------------------------------
// 基础设施
// ---------------------------------------------------------------------------
const token = ref(localStorage.getItem('hdz_token') || '');
const toast = reactive({ show: false, text: '', type: 'ok' });

function notify(text, type = 'ok') {
  toast.text = text;
  toast.type = type;
  toast.show = true;
  setTimeout(() => (toast.show = false), 2800);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Api-Token': token.value,
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    notify('鉴权失败，请填写正确的访问令牌', 'err');
    throw new Error('unauthorized');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `请求失败 ${res.status}`);
  }
  return data;
}

const get = (p) => api(p);
const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body || {}) });
const put = (p, body) => api(p, { method: 'PUT', body: JSON.stringify(body || {}) });
const patch = (p, body) => api(p, { method: 'PATCH', body: JSON.stringify(body || {}) });
const del = (p) => api(p, { method: 'DELETE' });

const fmtTime = (v) => (v ? String(v).replace('T', ' ').slice(0, 16) : '-');
const pct = (v) => `${Number(v || 0).toFixed(1)}%`;

const GRADE_STYLE = {
  S: 'bg-rose-50 text-rose-600',
  A: 'bg-amber-50 text-amber-600',
  B: 'bg-sky-50 text-sky-600',
  C: 'bg-slate-100 text-slate-500',
};
const LIFECYCLE_TEXT = {
  new: '新采集', reached: '已触达', engaged: '有互动', intent: '有意向',
  registered: '已注册', activated: '已下单', dormant: '沉默', invalid: '无效',
};
const STATUS_STYLE = {
  pending: 'bg-slate-100 text-slate-600', sending: 'bg-blue-50 text-blue-600',
  sent: 'bg-emerald-50 text-emerald-600', delivered: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-rose-50 text-rose-600', skipped: 'bg-amber-50 text-amber-600',
  running: 'bg-emerald-50 text-emerald-600', draft: 'bg-slate-100 text-slate-600',
  ready: 'bg-sky-50 text-sky-600', paused: 'bg-amber-50 text-amber-600',
  finished: 'bg-slate-100 text-slate-500', pending_: '',
};
const STATUS_TEXT = {
  pending: '待发', sending: '发送中', sent: '已提交', delivered: '已送达',
  failed: '失败', skipped: '已拦截', running: '进行中', draft: '草稿',
  ready: '待启动', paused: '已暂停', finished: '已结束',
};

// ---------------------------------------------------------------------------
// 概览
// ---------------------------------------------------------------------------
const Dashboard = {
  setup() {
    const overview = ref({});
    const funnel = ref([]);
    const trend = ref([]);
    const dist = ref([]);
    const dimension = ref('city');
    const loading = ref(true);

    async function load() {
      try {
        const [o, f, t, d] = await Promise.all([
          get('/api/stats/overview'),
          get('/api/stats/funnel'),
          get('/api/stats/trend?days=14'),
          get(`/api/stats/distribution?dimension=${dimension.value}`),
        ]);
        overview.value = o; funnel.value = f; trend.value = t; dist.value = d;
      } catch (e) { notify(e.message, 'err'); } finally { loading.value = false; }
    }
    watch(dimension, async () => {
      dist.value = await get(`/api/stats/distribution?dimension=${dimension.value}`);
    });
    onMounted(load);

    const maxTrend = computed(() =>
      Math.max(1, ...trend.value.flatMap((d) => [d.collected, d.sent, d.clicked])));
    const maxDist = computed(() => Math.max(1, ...dist.value.map((d) => d.value)));

    return { overview, funnel, trend, dist, dimension, loading, maxTrend, maxDist, pct, load,
      LIFECYCLE_TEXT };
  },
  template: `
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold">增长概览</h1>
        <p class="text-sm text-ink-500 mt-1">从高德捞到商户，到商户在货袋子下第一单，全链路一张图看清</p>
      </div>
      <button class="btn btn-ghost" @click="load">刷新</button>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card p-4">
        <div class="text-xs text-ink-500">商户库总量</div>
        <div class="text-2xl font-semibold mt-1">{{ overview.merchants || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">今日新增 {{ overview.today_collected || 0 }}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-ink-500">可短信触达</div>
        <div class="text-2xl font-semibold mt-1 text-brand-600">{{ overview.reachable || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">覆盖率 {{ pct(overview.reachable_rate) }}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-ink-500">累计发送</div>
        <div class="text-2xl font-semibold mt-1">{{ overview.sent || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">今日 {{ overview.today_sent || 0 }} · 待发 {{ overview.pending || 0 }}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-ink-500">点击率</div>
        <div class="text-2xl font-semibold mt-1 text-emerald-600">{{ pct(overview.click_rate) }}</div>
        <div class="text-xs text-ink-500 mt-1">{{ overview.clicked || 0 }} 次点击</div>
      </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="card p-4">
        <div class="text-xs text-ink-500">意向回复</div>
        <div class="text-xl font-semibold mt-1">{{ overview.interested || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">总回复 {{ overview.replies || 0 }}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-ink-500">注册开户</div>
        <div class="text-xl font-semibold mt-1">{{ overview.registered || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">转化率 {{ pct(overview.register_rate) }}</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-ink-500">完成首单</div>
        <div class="text-xl font-semibold mt-1">{{ overview.ordered || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">GMV {{ overview.gmv || 0 }} 元</div>
      </div>
      <div class="card p-4">
        <div class="text-xs text-ink-500">计费条数 / 黑名单</div>
        <div class="text-xl font-semibold mt-1">{{ overview.billing_count || 0 }}</div>
        <div class="text-xs text-ink-500 mt-1">退订黑名单 {{ overview.blacklist || 0 }}</div>
      </div>
    </div>

    <div class="grid lg:grid-cols-2 gap-5">
      <div class="card p-5">
        <h2 class="font-medium mb-1">转化漏斗</h2>
        <p class="text-xs text-ink-500 mb-4">哪一层掉得最狠，下一步就该优化哪里</p>
        <div class="space-y-3">
          <div v-for="s in funnel" :key="s.stage">
            <div class="flex justify-between text-xs mb-1.5">
              <span class="text-ink-700 font-medium">{{ s.stage }}</span>
              <span class="text-ink-500">{{ s.value }} · 上一层 {{ pct(s.rate_of_prev) }}</span>
            </div>
            <div class="bg-slate-100 rounded-lg">
              <div class="funnel-bar" :style="{ width: Math.max(s.rate_of_top, 1) + '%' }"></div>
            </div>
          </div>
        </div>
      </div>

      <div class="card p-5">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="font-medium">商户分布</h2>
            <p class="text-xs text-ink-500 mt-1">看清资源集中在哪，决定 BD 往哪投</p>
          </div>
          <select v-model="dimension" class="select w-28">
            <option value="city">按城市</option>
            <option value="district">按区县</option>
            <option value="category">按品类</option>
            <option value="grade">按评级</option>
            <option value="lifecycle">按阶段</option>
          </select>
        </div>
        <div class="space-y-2.5 max-h-72 overflow-y-auto">
          <div v-for="d in dist" :key="d.name" class="flex items-center gap-3">
            <div class="w-20 text-xs text-ink-700 truncate">{{ LIFECYCLE_TEXT[d.name] || d.name }}</div>
            <div class="flex-1 bg-slate-100 rounded h-5">
              <div class="h-5 rounded bg-brand-400" :style="{ width: (d.value / maxDist * 100) + '%' }"></div>
            </div>
            <div class="w-12 text-right text-xs text-ink-500">{{ d.value }}</div>
          </div>
          <div v-if="!dist.length" class="text-sm text-ink-500 py-6 text-center">暂无数据</div>
        </div>
      </div>
    </div>

    <div class="card p-5">
      <h2 class="font-medium mb-1">近 14 天走势</h2>
      <p class="text-xs text-ink-500 mb-4">
        <span class="inline-block w-2 h-2 rounded-full bg-slate-400 mr-1"></span>采集
        <span class="inline-block w-2 h-2 rounded-full bg-brand-500 ml-3 mr-1"></span>发送
        <span class="inline-block w-2 h-2 rounded-full bg-emerald-500 ml-3 mr-1"></span>点击
      </p>
      <div class="flex items-end gap-1 h-40">
        <div v-for="d in trend" :key="d.date" class="flex-1 flex flex-col items-center gap-1 group relative">
          <div class="w-full flex items-end justify-center gap-0.5 h-32">
            <div class="w-1/4 bg-slate-300 rounded-t" :style="{ height: (d.collected / maxTrend * 100) + '%' }"></div>
            <div class="w-1/4 bg-brand-500 rounded-t" :style="{ height: (d.sent / maxTrend * 100) + '%' }"></div>
            <div class="w-1/4 bg-emerald-500 rounded-t" :style="{ height: (d.clicked / maxTrend * 100) + '%' }"></div>
          </div>
          <div class="text-[10px] text-ink-500">{{ d.date.slice(5) }}</div>
          <div class="hidden group-hover:block absolute -top-14 bg-ink-900 text-white text-[11px] px-2 py-1 rounded whitespace-nowrap z-10">
            采集 {{ d.collected }} · 发送 {{ d.sent }} · 点击 {{ d.clicked }}
          </div>
        </div>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 商机采集
// ---------------------------------------------------------------------------
const Collect = {
  setup() {
    const health = ref({});
    const form = reactive({
      name: '', mode: 'text', keywordText: '', region: '', types: '',
      grid: 4, location: '', radius: 5000, city_limit: true,
    });
    const previewData = ref(null);
    const previewing = ref(false);
    const creating = ref(false);
    const tasks = ref([]);
    let timer = null;

    const keywords = computed(() =>
      form.keywordText.split(/[,，\n\s]+/).map((k) => k.trim()).filter(Boolean));

    async function loadTasks() {
      try { tasks.value = (await get('/api/collect/tasks?page_size=20')).items; } catch (e) {}
    }

    async function doPreview() {
      if (!keywords.value.length) return notify('请先填写关键词', 'err');
      previewing.value = true;
      try {
        previewData.value = await post('/api/collect/preview', {
          keyword: keywords.value[0], region: form.region || null,
          types: form.types || null, limit: 10,
        });
      } catch (e) { notify(e.message, 'err'); } finally { previewing.value = false; }
    }

    async function createTask() {
      if (!keywords.value.length) return notify('请先填写关键词', 'err');
      creating.value = true;
      try {
        await post('/api/collect/tasks', {
          name: form.name || `${form.region || '全国'}-${keywords.value[0]}`,
          mode: form.mode, keywords: keywords.value, region: form.region || null,
          types: form.types || null, grid: form.grid,
          location: form.location || null, radius: form.radius, city_limit: form.city_limit,
        });
        notify('采集任务已启动，进度会自动刷新');
        form.name = '';
        await loadTasks();
      } catch (e) { notify(e.message, 'err'); } finally { creating.value = false; }
    }

    async function retry(id) {
      try { await post(`/api/collect/tasks/${id}/retry`); notify('已重新启动'); loadTasks(); }
      catch (e) { notify(e.message, 'err'); }
    }

    onMounted(async () => {
      loadTasks();
      try { health.value = await get('/api/collect/health'); } catch (e) {}
      timer = setInterval(loadTasks, 4000);
    });
    onUnmounted(() => clearInterval(timer));

    return { health, form, keywords, previewData, previewing, creating, tasks,
      doPreview, createTask, retry, fmtTime, STATUS_STYLE, STATUS_TEXT, GRADE_STYLE };
  },
  template: `
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold">商机采集</h1>
      <p class="text-sm text-ink-500 mt-1">输入关键词，从高德捞出目标商户与联系方式，自动清洗去重并按价值打分</p>
    </div>

    <div v-if="health.ok === false" class="card p-4 border-amber-200 bg-amber-50 text-sm text-amber-800">
      高德接口不可用：{{ health.message }}
    </div>

    <div class="grid lg:grid-cols-5 gap-5">
      <div class="card p-5 lg:col-span-2 space-y-4">
        <h2 class="font-medium">新建采集任务</h2>

        <div>
          <label class="label">采集方式</label>
          <div class="grid grid-cols-3 gap-2">
            <button v-for="m in [
              {k:'text',t:'关键词',d:'城市内直接搜'},
              {k:'grid',t:'网格化',d:'突破1000条上限'},
              {k:'around',t:'周边',d:'圈定商圈扫街'}]"
              :key="m.k" @click="form.mode = m.k"
              :class="['border rounded-lg px-2 py-2 text-center transition',
                form.mode===m.k ? 'border-brand-500 bg-brand-50 text-brand-600' : 'border-slate-200 text-ink-700']">
              <div class="text-xs font-medium">{{ m.t }}</div>
              <div class="text-[10px] text-ink-500 mt-0.5">{{ m.d }}</div>
            </button>
          </div>
        </div>

        <div>
          <label class="label">关键词（多个用逗号或换行分隔）</label>
          <textarea v-model="form.keywordText" rows="3" class="textarea"
            placeholder="火锅店&#10;烧烤&#10;生鲜超市&#10;食堂"></textarea>
          <p class="text-xs text-ink-500 mt-1.5" v-if="keywords.length">
            共 {{ keywords.length }} 个关键词，将逐个采集
          </p>
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="label">城市</label>
            <input v-model="form.region" class="input" placeholder="杭州" />
          </div>
          <div>
            <label class="label">任务名称（选填）</label>
            <input v-model="form.name" class="input" placeholder="自动生成" />
          </div>
        </div>

        <div v-if="form.mode==='grid'">
          <label class="label">网格密度：{{ form.grid }} × {{ form.grid }} = {{ form.grid*form.grid }} 格</label>
          <input type="range" v-model.number="form.grid" min="2" max="10" class="w-full" />
          <p class="text-xs text-ink-500">格子越密覆盖越全，但消耗的高德配额也成倍增加</p>
        </div>

        <div v-if="form.mode==='around'" class="grid grid-cols-2 gap-3">
          <div>
            <label class="label">中心点坐标</label>
            <input v-model="form.location" class="input" placeholder="120.15,30.28" />
          </div>
          <div>
            <label class="label">半径（米）</label>
            <input type="number" v-model.number="form.radius" class="input" />
          </div>
        </div>

        <div>
          <label class="label">POI 类型编码（选填）</label>
          <input v-model="form.types" class="input" placeholder="050000 餐饮 | 060000 购物" />
        </div>

        <div class="flex gap-2 pt-1">
          <button class="btn btn-ghost flex-1" @click="doPreview" :disabled="previewing">
            {{ previewing ? '试搜中...' : '先试搜 10 条' }}
          </button>
          <button class="btn btn-primary flex-1" @click="createTask" :disabled="creating">
            {{ creating ? '提交中...' : '开始采集' }}
          </button>
        </div>
      </div>

      <div class="card p-5 lg:col-span-3">
        <h2 class="font-medium mb-1">试搜结果</h2>
        <p class="text-xs text-ink-500 mb-4">
          不同品类在高德挂手机号的比例差很多。先看这批的手机号覆盖率，再决定要不要全量跑
        </p>
        <div v-if="!previewData" class="text-sm text-ink-500 py-16 text-center">
          填好关键词后点「先试搜 10 条」
        </div>
        <div v-else>
          <div class="flex gap-4 mb-4 text-sm">
            <div class="px-3 py-2 bg-slate-50 rounded-lg">
              返回 <b>{{ previewData.total }}</b> 条
            </div>
            <div class="px-3 py-2 rounded-lg"
              :class="previewData.mobile_rate >= 30 ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'">
              手机号覆盖率 <b>{{ previewData.mobile_rate }}%</b>
              <span class="text-xs ml-1">{{ previewData.mobile_rate >= 30 ? '值得投入' : '偏低，建议换词或转 BD 扫街' }}</span>
            </div>
          </div>
          <div class="overflow-x-auto max-h-96 overflow-y-auto">
            <table class="data">
              <thead><tr><th>商户</th><th>评级</th><th>号码</th><th>标签</th></tr></thead>
              <tbody>
                <tr v-for="(m, i) in previewData.items" :key="i">
                  <td>
                    <div class="font-medium">{{ m.name }}</div>
                    <div class="text-xs text-ink-500 mt-0.5">{{ m.district }} · {{ m.type_name }}</div>
                  </td>
                  <td><span class="tag" :class="GRADE_STYLE[m.grade]">{{ m.grade }} {{ m.score }}</span></td>
                  <td>
                    <div v-for="p in m.phones" :key="p.phone" class="text-xs">
                      {{ p.masked }}
                      <span class="text-[10px]" :class="p.phone_type==='mobile' ? 'text-emerald-600' : 'text-ink-500'">
                        {{ p.phone_type === 'mobile' ? '手机' : '座机' }}
                      </span>
                    </div>
                    <span v-if="!m.phones.length" class="text-xs text-ink-500">无</span>
                  </td>
                  <td><span v-for="t in (m.tags||[]).slice(0,3)" :key="t"
                    class="tag bg-slate-100 text-slate-600 mr-1">{{ t }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="px-5 py-4 border-b border-slate-100">
        <h2 class="font-medium">采集任务</h2>
      </div>
      <div class="overflow-x-auto">
        <table class="data">
          <thead><tr>
            <th>任务</th><th>方式</th><th>进度</th><th>抓取 / 入库</th>
            <th>可触达</th><th>说明</th><th>创建时间</th><th></th>
          </tr></thead>
          <tbody>
            <tr v-for="t in tasks" :key="t.id">
              <td>
                <div class="font-medium">{{ t.name }}</div>
                <div class="text-xs text-ink-500 mt-0.5">{{ t.region }} · {{ (t.keywords||[]).join('、') }}</div>
              </td>
              <td class="text-xs">{{ {text:'关键词',grid:'网格化',around:'周边'}[t.mode] }}</td>
              <td class="w-32">
                <span class="tag" :class="STATUS_STYLE[t.status] || 'bg-slate-100 text-slate-600'">
                  {{ {pending:'排队中',running:'采集中',finished:'已完成',failed:'失败'}[t.status] }}
                </span>
                <div class="bg-slate-100 rounded h-1.5 mt-1.5">
                  <div class="bg-brand-500 h-1.5 rounded transition-all" :style="{width: t.progress+'%'}"></div>
                </div>
              </td>
              <td class="text-xs">{{ t.total_fetched }} / <b>{{ t.total_saved }}</b>
                <div class="text-ink-500">去重 {{ t.total_duplicated }}</div></td>
              <td class="text-xs text-emerald-600 font-medium">{{ t.total_mobile }}</td>
              <td class="text-xs text-ink-500 max-w-xs">{{ t.message }}</td>
              <td class="text-xs text-ink-500">{{ fmtTime(t.created_at) }}</td>
              <td><button v-if="t.status==='failed'" class="btn btn-ghost btn-sm" @click="retry(t.id)">重试</button></td>
            </tr>
            <tr v-if="!tasks.length"><td colspan="8" class="text-center text-ink-500 py-10">还没有采集任务</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 商户库
// ---------------------------------------------------------------------------
const Merchants = {
  setup() {
    const filters = reactive({ city: '', category: '', grade: '', lifecycle: '',
      keyword: '', has_mobile: true, page: 1, page_size: 20, order_by: 'score' });
    const data = ref({ items: [], total: 0 });
    const options = ref({ cities: [], categories: [], grades: [], lifecycles: [] });
    const detail = ref(null);
    const loading = ref(false);

    async function load() {
      loading.value = true;
      const qs = new URLSearchParams(
        Object.entries(filters).filter(([, v]) => v !== '' && v !== null)).toString();
      try { data.value = await get(`/api/merchants?${qs}`); }
      catch (e) { notify(e.message, 'err'); } finally { loading.value = false; }
    }
    async function openDetail(id) {
      try { detail.value = await get(`/api/merchants/${id}`); } catch (e) { notify(e.message, 'err'); }
    }
    async function updateLifecycle(id, lifecycle) {
      try {
        await patch(`/api/merchants/${id}`, { lifecycle });
        notify('已更新');
        if (detail.value) detail.value.merchant.lifecycle = lifecycle;
        load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function exportCsv() {
      const qs = new URLSearchParams({ city: filters.city, category: filters.category,
        grade: filters.grade, lifecycle: filters.lifecycle, has_mobile: filters.has_mobile }).toString();
      try {
        const res = await fetch(`/api/merchants/export/csv?${qs}`,
          { headers: { 'X-Api-Token': token.value } });
        if (!res.ok) throw new Error('导出失败，请检查访问令牌');
        const url = URL.createObjectURL(await res.blob());
        const a = document.createElement('a');
        a.href = url;
        a.download = `商户名单_${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
        notify('导出为脱敏名单，需要完整号码请走审批后加 unmask 参数');
      } catch (e) { notify(e.message, 'err'); }
    }

    watch(() => [filters.city, filters.category, filters.grade, filters.lifecycle, filters.has_mobile],
      () => { filters.page = 1; load(); });
    onMounted(async () => {
      load();
      try { options.value = await get('/api/merchants/filters'); } catch (e) {}
    });

    const totalPages = computed(() => Math.ceil(data.value.total / filters.page_size) || 1);
    return { filters, data, options, detail, loading, load, openDetail, updateLifecycle,
      exportCsv, totalPages, fmtTime, GRADE_STYLE, LIFECYCLE_TEXT, STATUS_STYLE, STATUS_TEXT };
  },
  template: `
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold">商户库</h1>
        <p class="text-sm text-ink-500 mt-1">按价值分级的商户资产，S/A 级优先短信 + BD 双线跟进</p>
      </div>
      <button class="btn btn-ghost" @click="exportCsv">导出 BD 名单</button>
    </div>

    <div class="card p-4">
      <div class="grid md:grid-cols-6 gap-3">
        <input v-model="filters.keyword" @keyup.enter="filters.page=1; load()" class="input" placeholder="搜索商户名 / 地址" />
        <select v-model="filters.city" class="select"><option value="">全部城市</option>
          <option v-for="c in options.cities" :key="c" :value="c">{{ c }}</option></select>
        <select v-model="filters.category" class="select"><option value="">全部品类</option>
          <option v-for="c in options.categories" :key="c" :value="c">{{ c }}</option></select>
        <select v-model="filters.grade" class="select"><option value="">全部评级</option>
          <option v-for="g in options.grades" :key="g" :value="g">{{ g }} 级</option></select>
        <select v-model="filters.lifecycle" class="select"><option value="">全部阶段</option>
          <option v-for="l in options.lifecycles" :key="l" :value="l">{{ LIFECYCLE_TEXT[l] }}</option></select>
        <label class="flex items-center gap-2 text-sm text-ink-700">
          <input type="checkbox" v-model="filters.has_mobile" class="rounded" /> 仅可触达
        </label>
      </div>
    </div>

    <div class="card">
      <div class="overflow-x-auto">
        <table class="data">
          <thead><tr>
            <th>商户</th><th>评级</th><th>品类</th><th>位置</th><th>手机号</th>
            <th>评分/人均</th><th>阶段</th><th>触达</th><th></th>
          </tr></thead>
          <tbody>
            <tr v-for="m in data.items" :key="m.id">
              <td>
                <div class="font-medium">{{ m.name }}</div>
                <div class="text-xs text-ink-500 mt-0.5 max-w-xs truncate">{{ m.address }}</div>
              </td>
              <td><span class="tag" :class="GRADE_STYLE[m.grade]">{{ m.grade }} · {{ m.score }}</span></td>
              <td class="text-xs">{{ m.category }}</td>
              <td class="text-xs">{{ m.city }}<div class="text-ink-500">{{ m.business_area || m.district }}</div></td>
              <td class="text-xs">
                <div v-for="p in m.phones.filter(p=>p.phone_type==='mobile')" :key="p.phone">{{ p.phone }}</div>
                <span v-if="!m.phones.some(p=>p.phone_type==='mobile')" class="text-ink-500">仅座机</span>
              </td>
              <td class="text-xs">{{ m.rating || '-' }}<div class="text-ink-500">{{ m.cost ? m.cost+'元' : '' }}</div></td>
              <td><span class="tag bg-slate-100 text-slate-600">{{ LIFECYCLE_TEXT[m.lifecycle] }}</span></td>
              <td class="text-xs">{{ m.touch_count }} 次<div class="text-ink-500">{{ fmtTime(m.last_touch_at) }}</div></td>
              <td><button class="btn btn-ghost btn-sm" @click="openDetail(m.id)">详情</button></td>
            </tr>
            <tr v-if="!data.items.length"><td colspan="9" class="text-center text-ink-500 py-12">
              暂无商户，先去「商机采集」跑一批</td></tr>
          </tbody>
        </table>
      </div>
      <div class="flex items-center justify-between px-5 py-3 border-t border-slate-100 text-sm">
        <span class="text-ink-500">共 {{ data.total }} 家</span>
        <div class="flex items-center gap-2">
          <button class="btn btn-ghost btn-sm" :disabled="filters.page<=1"
            @click="filters.page--; load()">上一页</button>
          <span class="text-xs text-ink-500">{{ filters.page }} / {{ totalPages }}</span>
          <button class="btn btn-ghost btn-sm" :disabled="filters.page>=totalPages"
            @click="filters.page++; load()">下一页</button>
        </div>
      </div>
    </div>

    <div v-if="detail" class="modal-mask" @click.self="detail=null">
      <div class="modal max-w-2xl p-6">
        <div class="flex items-start justify-between mb-4">
          <div>
            <h2 class="text-lg font-semibold">{{ detail.merchant.name }}</h2>
            <p class="text-sm text-ink-500 mt-1">{{ detail.merchant.address }}</p>
          </div>
          <button class="btn btn-ghost btn-sm" @click="detail=null">关闭</button>
        </div>

        <div class="grid grid-cols-4 gap-3 mb-5">
          <div class="bg-slate-50 rounded-lg p-3">
            <div class="text-xs text-ink-500">价值评分</div>
            <div class="font-semibold mt-1">{{ detail.merchant.grade }} · {{ detail.merchant.score }}</div>
          </div>
          <div class="bg-slate-50 rounded-lg p-3">
            <div class="text-xs text-ink-500">品类</div>
            <div class="font-semibold mt-1">{{ detail.merchant.category }}</div>
          </div>
          <div class="bg-slate-50 rounded-lg p-3">
            <div class="text-xs text-ink-500">评分 / 人均</div>
            <div class="font-semibold mt-1">{{ detail.merchant.rating || '-' }} / {{ detail.merchant.cost || '-' }}</div>
          </div>
          <div class="bg-slate-50 rounded-lg p-3">
            <div class="text-xs text-ink-500">触达次数</div>
            <div class="font-semibold mt-1">{{ detail.merchant.touch_count }}</div>
          </div>
        </div>

        <div class="mb-5">
          <div class="label">联系方式</div>
          <div class="flex flex-wrap gap-2">
            <span v-for="p in detail.merchant.phones" :key="p.phone"
              class="tag" :class="p.phone_type==='mobile' ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-600'">
              {{ p.phone }} · {{ p.phone_type === 'mobile' ? '手机' : '座机' }}
            </span>
          </div>
        </div>

        <div class="mb-5">
          <div class="label">推进到阶段</div>
          <div class="flex flex-wrap gap-2">
            <button v-for="(text, key) in LIFECYCLE_TEXT" :key="key"
              @click="updateLifecycle(detail.merchant.id, key)"
              :class="['btn btn-sm', detail.merchant.lifecycle===key ? 'btn-primary' : 'btn-ghost']">
              {{ text }}
            </button>
          </div>
        </div>

        <div>
          <div class="label">触达历史</div>
          <div class="space-y-2 max-h-56 overflow-y-auto">
            <div v-for="h in detail.touch_history" :key="h.id" class="bg-slate-50 rounded-lg p-3">
              <div class="flex items-center justify-between mb-1">
                <span class="tag" :class="STATUS_STYLE[h.status]">{{ STATUS_TEXT[h.status] }}</span>
                <span class="text-xs text-ink-500">第 {{ h.round_no }} 轮 · {{ fmtTime(h.sent_at) }}</span>
              </div>
              <div class="text-xs text-ink-700">{{ h.content || h.skip_reason }}</div>
            </div>
            <div v-if="!detail.touch_history.length" class="text-sm text-ink-500 py-4 text-center">尚未触达</div>
          </div>
        </div>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 短信模板
// ---------------------------------------------------------------------------
const Templates = {
  setup() {
    const list = ref([]);
    const variables = ref({});
    const editing = ref(null);
    const preview = ref(null);
    let debounce = null;

    const empty = () => ({ id: null, name: '', content: '', sign: '', scene: 'first_touch',
      with_link: true, landing_url: '', remark: '', status: 'enabled' });

    async function load() {
      try { list.value = await get('/api/templates'); } catch (e) { notify(e.message, 'err'); }
    }
    function edit(t) { editing.value = t ? { ...t } : empty(); doPreview(); }

    async function doPreview() {
      clearTimeout(debounce);
      debounce = setTimeout(async () => {
        if (!editing.value?.content) { preview.value = null; return; }
        try {
          preview.value = await post('/api/templates/preview', {
            content: editing.value.content, sign: editing.value.sign || null });
        } catch (e) {}
      }, 350);
    }
    watch(() => editing.value?.content, doPreview);
    watch(() => editing.value?.sign, doPreview);

    async function save() {
      const t = editing.value;
      if (!t.name || !t.content) return notify('模板名和内容不能为空', 'err');
      try {
        const body = { name: t.name, content: t.content, sign: t.sign || null, scene: t.scene,
          with_link: t.with_link, landing_url: t.landing_url || null,
          remark: t.remark || null, status: t.status };
        if (t.id) await put(`/api/templates/${t.id}`, body);
        else await post('/api/templates', body);
        notify('已保存');
        editing.value = null;
        load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function remove(id) {
      if (!confirm('确认删除该模板？')) return;
      try { const r = await del(`/api/templates/${id}`); notify(r.message || '已删除'); load(); }
      catch (e) { notify(e.message, 'err'); }
    }
    function insertVar(name) {
      editing.value.content = (editing.value.content || '') + `{${name}}`;
      doPreview();
    }

    onMounted(async () => {
      load();
      try { variables.value = (await get('/api/templates/variables')).variables; } catch (e) {}
    });

    const SCENE_TEXT = { first_touch: '首次触达', follow_up: '跟进触达',
      reactivate: '沉默唤醒', promotion: '活动promo' };
    return { list, variables, editing, preview, edit, save, remove, insertVar, empty, SCENE_TEXT };
  },
  template: `
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold">短信模板</h1>
        <p class="text-sm text-ink-500 mt-1">
          带上门店真名的个性化文案，回复率明显高于通用群发。系统已预置三轮触达文案
        </p>
      </div>
      <button class="btn btn-primary" @click="edit(null)">新建模板</button>
    </div>

    <div class="grid md:grid-cols-2 gap-4">
      <div v-for="t in list" :key="t.id" class="card p-5">
        <div class="flex items-start justify-between mb-3">
          <div>
            <div class="font-medium">{{ t.name }}</div>
            <div class="text-xs text-ink-500 mt-1">
              {{ SCENE_TEXT[t.scene] || t.scene }} ·
              <span :class="t.status==='enabled' ? 'text-emerald-600' : 'text-ink-500'">
                {{ t.status === 'enabled' ? '启用中' : '已停用' }}</span>
            </div>
          </div>
          <div class="flex gap-1">
            <button class="btn btn-ghost btn-sm" @click="edit(t)">编辑</button>
            <button class="btn btn-danger btn-sm" @click="remove(t.id)">删除</button>
          </div>
        </div>
        <div class="bg-slate-50 rounded-lg p-3 text-sm leading-relaxed text-ink-700">{{ t.preview }}</div>
        <div class="flex items-center gap-3 mt-3 text-xs text-ink-500">
          <span>{{ t.preview.length }} 字 · 计 {{ t.billing_count }} 条</span>
          <span v-for="v in t.variables" :key="v" class="tag bg-brand-50 text-brand-600">{{ v }}</span>
        </div>
      </div>
      <div v-if="!list.length" class="card p-12 text-center text-ink-500 md:col-span-2">暂无模板</div>
    </div>

    <div v-if="editing" class="modal-mask" @click.self="editing=null">
      <div class="modal max-w-3xl p-6">
        <h2 class="text-lg font-semibold mb-5">{{ editing.id ? '编辑模板' : '新建模板' }}</h2>
        <div class="grid md:grid-cols-2 gap-5">
          <div class="space-y-4">
            <div class="grid grid-cols-2 gap-3">
              <div><label class="label">模板名称</label>
                <input v-model="editing.name" class="input" placeholder="首触-源头直采省成本" /></div>
              <div><label class="label">使用场景</label>
                <select v-model="editing.scene" class="select">
                  <option v-for="(text, key) in SCENE_TEXT" :key="key" :value="key">{{ text }}</option>
                </select></div>
            </div>
            <div><label class="label">短信签名（留空用全局签名）</label>
              <input v-model="editing.sign" class="input" placeholder="【货袋子】" /></div>
            <div>
              <label class="label">短信正文</label>
              <textarea v-model="editing.content" rows="5" class="textarea"
                placeholder="{商家名}老板您好，货袋子{品类}产地直采已在{城市}上线..."></textarea>
            </div>
            <div>
              <label class="label">插入个性化变量</label>
              <div class="flex flex-wrap gap-1.5">
                <button v-for="(desc, name) in variables" :key="name" @click="insertVar(name)"
                  :title="desc" class="tag bg-brand-50 text-brand-600 hover:bg-brand-100">{{ name }}</button>
              </div>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div><label class="label">落地页地址</label>
                <input v-model="editing.landing_url" class="input" placeholder="https://m.huodaizi.com/reg" /></div>
              <div class="flex items-end pb-1.5">
                <label class="flex items-center gap-2 text-sm text-ink-700">
                  <input type="checkbox" v-model="editing.with_link" class="rounded" /> 生成专属追踪短链
                </label>
              </div>
            </div>
          </div>

          <div class="space-y-4">
            <div>
              <label class="label">实际下发效果预览</label>
              <div class="bg-slate-800 rounded-xl p-4">
                <div class="bg-white rounded-lg p-3 text-sm leading-relaxed">
                  {{ preview?.preview || '开始输入正文即可预览' }}
                </div>
                <div class="text-xs text-slate-400 mt-2 text-center">
                  {{ preview?.length || 0 }} 字 · 按 {{ preview?.billing_count || 0 }} 条计费
                </div>
              </div>
            </div>
            <div v-if="preview?.warnings?.length" class="bg-amber-50 border border-amber-200 rounded-lg p-3">
              <div class="text-xs font-medium text-amber-800 mb-2">发送前提醒</div>
              <ul class="text-xs text-amber-700 space-y-1">
                <li v-for="w in preview.warnings" :key="w">· {{ w }}</li>
              </ul>
            </div>
            <div class="bg-slate-50 rounded-lg p-3 text-xs text-ink-500 leading-relaxed">
              写文案的三个要点：第一句点名门店，让老板知道不是群发；
              中间给可量化的利益点（省多少钱、优惠多少），别讲抽象的"优质货源"；
              结尾给一个低门槛动作（点链接看报价），不要一上来就要求注册。
            </div>
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-6">
          <button class="btn btn-ghost" @click="editing=null">取消</button>
          <button class="btn btn-primary" @click="save">保存模板</button>
        </div>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 触达活动
// ---------------------------------------------------------------------------
const Campaigns = {
  setup() {
    const list = ref([]);
    const templates = ref([]);
    const options = ref({ cities: [], categories: [], grades: [], lifecycles: [] });
    const creating = ref(false);
    const report = ref(null);
    const audienceCount = ref(null);
    const form = reactive({
      name: '', goal: '', landing_url: '', daily_limit: 2000,
      audience: { city: '', category: '', grades: [], lifecycles: [], min_score: null, never_touched: true },
      variants: [{ template_id: null, weight: 1, label: 'A' }],
      follow_up: { enabled: false, rounds: [] },
    });
    let timer = null;

    async function load() {
      try {
        const r = await get('/api/campaigns?page_size=50');
        list.value = r.items;
      } catch (e) {}
    }
    async function estimate() {
      try {
        const payload = Object.fromEntries(
          Object.entries(form.audience).filter(([, v]) =>
            v !== '' && v !== null && !(Array.isArray(v) && !v.length)));
        audienceCount.value = (await post('/api/campaigns/audience/count', payload)).total;
      } catch (e) { notify(e.message, 'err'); }
    }
    function addVariant() {
      form.variants.push({ template_id: null, weight: 1,
        label: String.fromCharCode(65 + form.variants.length) });
    }
    function addRound() {
      form.follow_up.rounds.push({ after_days: form.follow_up.rounds.length ? 7 : 3, template_id: null });
    }
    async function create() {
      if (!form.name) return notify('请填写活动名称', 'err');
      if (form.variants.some(v => !v.template_id)) return notify('请为每个文案版本选择模板', 'err');
      try {
        const audience = Object.fromEntries(
          Object.entries(form.audience).filter(([, v]) =>
            v !== '' && v !== null && !(Array.isArray(v) && !v.length)));
        await post('/api/campaigns', {
          name: form.name, goal: form.goal || null, landing_url: form.landing_url || null,
          daily_limit: form.daily_limit, audience,
          variants: form.variants,
          follow_up: { enabled: form.follow_up.enabled,
            rounds: form.follow_up.rounds.filter(r => r.template_id) },
        });
        notify('活动已创建，下一步点「圈人」生成待发队列');
        creating.value = false;
        load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function action(id, act) {
      try {
        const r = await post(`/api/campaigns/${id}/${act}`);
        if (act === 'prepare') {
          notify(`圈人完成：入队 ${r.created} 条，合规拦截 ${r.skipped} 条，预计计费 ${r.billing_count} 条`);
        } else if (act === 'dispatch') {
          notify(`已提交 ${r.sent} 条`);
        } else {
          notify(r.message || '操作成功');
        }
        load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function openReport(id) {
      try { report.value = await get(`/api/campaigns/${id}/report`); } catch (e) { notify(e.message, 'err'); }
    }

    onMounted(async () => {
      load();
      try {
        templates.value = await get('/api/templates');
        options.value = await get('/api/merchants/filters');
      } catch (e) {}
      timer = setInterval(load, 6000);
    });
    onUnmounted(() => clearInterval(timer));

    return { list, templates, options, creating, form, audienceCount, report,
      load, estimate, addVariant, addRound, create, action, openReport,
      fmtTime, STATUS_STYLE, STATUS_TEXT, LIFECYCLE_TEXT, pct };
  },
  template: `
  <div class="space-y-5">
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold">触达活动</h1>
        <p class="text-sm text-ink-500 mt-1">
          圈人 → A/B 分流 → 按节奏发送 → 多轮跟进，每一步都有合规和频控守门
        </p>
      </div>
      <button class="btn btn-primary" @click="creating=true">新建活动</button>
    </div>

    <div class="card">
      <div class="overflow-x-auto">
        <table class="data">
          <thead><tr>
            <th>活动</th><th>状态</th><th>队列</th><th>发送情况</th>
            <th>日限额</th><th>创建时间</th><th>操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="c in list" :key="c.id">
              <td>
                <div class="font-medium">{{ c.name }}</div>
                <div class="text-xs text-ink-500 mt-0.5">{{ c.goal }}</div>
              </td>
              <td><span class="tag" :class="STATUS_STYLE[c.status]">{{ STATUS_TEXT[c.status] }}</span></td>
              <td class="text-xs">待发 <b>{{ c.counts?.pending || 0 }}</b>
                <div class="text-amber-600">拦截 {{ c.counts?.skipped || 0 }}</div></td>
              <td class="text-xs">已发 <b>{{ (c.counts?.sent||0) + (c.counts?.delivered||0) }}</b>
                <div class="text-rose-600" v-if="c.counts?.failed">失败 {{ c.counts.failed }}</div></td>
              <td class="text-xs">{{ c.daily_limit }}</td>
              <td class="text-xs text-ink-500">{{ fmtTime(c.created_at) }}</td>
              <td>
                <div class="flex gap-1 flex-wrap">
                  <button class="btn btn-ghost btn-sm" @click="action(c.id,'prepare')"
                    v-if="['draft','ready','paused'].includes(c.status)">圈人</button>
                  <button class="btn btn-primary btn-sm" @click="action(c.id,'start')"
                    v-if="['ready','paused'].includes(c.status) && c.counts?.pending">启动</button>
                  <button class="btn btn-ghost btn-sm" @click="action(c.id,'pause')"
                    v-if="c.status==='running'">暂停</button>
                  <button class="btn btn-ghost btn-sm" @click="action(c.id,'dispatch')"
                    v-if="c.status==='running'">立即发一批</button>
                  <button class="btn btn-ghost btn-sm" @click="openReport(c.id)">报表</button>
                </div>
              </td>
            </tr>
            <tr v-if="!list.length"><td colspan="7" class="text-center text-ink-500 py-12">
              还没有活动。建议第一次只圈 200 家 S 级商户做灰度，看点击率再决定放量</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 新建活动 -->
    <div v-if="creating" class="modal-mask" @click.self="creating=false">
      <div class="modal max-w-4xl p-6">
        <h2 class="text-lg font-semibold mb-5">新建触达活动</h2>
        <div class="grid md:grid-cols-2 gap-6">
          <div class="space-y-4">
            <div class="grid grid-cols-2 gap-3">
              <div><label class="label">活动名称</label>
                <input v-model="form.name" class="input" placeholder="杭州餐饮首轮拉新" /></div>
              <div><label class="label">日发送上限</label>
                <input type="number" v-model.number="form.daily_limit" class="input" /></div>
            </div>
            <div><label class="label">活动目标</label>
              <input v-model="form.goal" class="input" placeholder="拉动 200 家餐饮门店注册并完成首单" /></div>
            <div><label class="label">落地页地址</label>
              <input v-model="form.landing_url" class="input" placeholder="https://m.huodaizi.com/reg" /></div>

            <div class="border-t border-slate-100 pt-4">
              <div class="flex items-center justify-between mb-3">
                <div class="label mb-0">圈选受众</div>
                <button class="btn btn-ghost btn-sm" @click="estimate">预估人数</button>
              </div>
              <div class="grid grid-cols-2 gap-3">
                <select v-model="form.audience.city" class="select"><option value="">全部城市</option>
                  <option v-for="c in options.cities" :key="c" :value="c">{{ c }}</option></select>
                <select v-model="form.audience.category" class="select"><option value="">全部品类</option>
                  <option v-for="c in options.categories" :key="c" :value="c">{{ c }}</option></select>
              </div>
              <div class="mt-3">
                <div class="text-xs text-ink-500 mb-1.5">商户评级（不选＝全部）</div>
                <div class="flex gap-2">
                  <label v-for="g in ['S','A','B','C']" :key="g"
                    class="flex items-center gap-1.5 text-sm px-3 py-1.5 border rounded-lg cursor-pointer"
                    :class="form.audience.grades.includes(g) ? 'border-brand-500 bg-brand-50 text-brand-600' : 'border-slate-200'">
                    <input type="checkbox" :value="g" v-model="form.audience.grades" class="hidden" />{{ g }} 级
                  </label>
                </div>
              </div>
              <label class="flex items-center gap-2 text-sm text-ink-700 mt-3">
                <input type="checkbox" v-model="form.audience.never_touched" class="rounded" />
                只发从未触达过的商户
              </label>
              <div v-if="audienceCount !== null"
                class="mt-3 bg-brand-50 text-brand-700 rounded-lg px-3 py-2 text-sm">
                符合条件 <b>{{ audienceCount }}</b> 家，预计消耗 {{ audienceCount }} 条短信额度
                <div class="text-xs mt-1 text-brand-600">实际发送量会再扣掉黑名单与频控拦截的部分</div>
              </div>
            </div>
          </div>

          <div class="space-y-4">
            <div>
              <div class="flex items-center justify-between mb-2">
                <div class="label mb-0">文案 A/B 分组</div>
                <button class="btn btn-ghost btn-sm" @click="addVariant">增加一版</button>
              </div>
              <p class="text-xs text-ink-500 mb-3">
                同一批人按权重随机分到不同文案，发够量后看哪版点击率高，再把预算压到赢的那版
              </p>
              <div v-for="(v, i) in form.variants" :key="i" class="flex gap-2 mb-2">
                <input v-model="v.label" class="input w-16" placeholder="A" />
                <select v-model.number="v.template_id" class="select flex-1">
                  <option :value="null">选择模板</option>
                  <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
                </select>
                <input type="number" v-model.number="v.weight" class="input w-20" placeholder="权重" />
                <button class="btn btn-danger btn-sm" @click="form.variants.splice(i,1)"
                  v-if="form.variants.length>1">删</button>
              </div>
            </div>

            <div class="border-t border-slate-100 pt-4">
              <label class="flex items-center gap-2 text-sm font-medium text-ink-700 mb-2">
                <input type="checkbox" v-model="form.follow_up.enabled" class="rounded" />
                开启多轮跟进
              </label>
              <p class="text-xs text-ink-500 mb-3">
                首触讲价值，二触给具体让利，三触做同商圈案例。只对没点过链接的商户跟进，
                已经有互动的交给 BD 跟人，不再用短信打扰
              </p>
              <div v-if="form.follow_up.enabled">
                <div v-for="(r, i) in form.follow_up.rounds" :key="i" class="flex gap-2 mb-2 items-center">
                  <span class="text-xs text-ink-500 whitespace-nowrap">第 {{ i+2 }} 轮</span>
                  <input type="number" v-model.number="r.after_days" class="input w-20" />
                  <span class="text-xs text-ink-500 whitespace-nowrap">天后</span>
                  <select v-model.number="r.template_id" class="select flex-1">
                    <option :value="null">选择模板</option>
                    <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
                  </select>
                  <button class="btn btn-danger btn-sm" @click="form.follow_up.rounds.splice(i,1)">删</button>
                </div>
                <button class="btn btn-ghost btn-sm" @click="addRound">增加一轮</button>
              </div>
            </div>
          </div>
        </div>
        <div class="flex justify-end gap-2 mt-6">
          <button class="btn btn-ghost" @click="creating=false">取消</button>
          <button class="btn btn-primary" @click="create">创建活动</button>
        </div>
      </div>
    </div>

    <!-- 活动报表 -->
    <div v-if="report" class="modal-mask" @click.self="report=null">
      <div class="modal max-w-3xl p-6">
        <div class="flex items-center justify-between mb-5">
          <h2 class="text-lg font-semibold">活动效果报表</h2>
          <button class="btn btn-ghost btn-sm" @click="report=null">关闭</button>
        </div>

        <div class="mb-6">
          <div class="label">A/B 文案对比</div>
          <table class="data">
            <thead><tr><th>版本</th><th>入队</th><th>已发</th><th>送达</th><th>失败</th><th>点击</th><th>点击率</th></tr></thead>
            <tbody>
              <tr v-for="v in report.variants" :key="v.variant">
                <td class="font-medium">{{ v.variant }}
                  <span v-if="report.winner===v.variant" class="tag bg-emerald-50 text-emerald-600 ml-1">领先</span>
                </td>
                <td>{{ v.total }}</td><td>{{ v.sent }}</td><td>{{ v.delivered }}</td>
                <td :class="v.failed ? 'text-rose-600' : ''">{{ v.failed }}</td>
                <td>{{ v.clicked }}</td>
                <td class="font-medium text-emerald-600">{{ pct(v.click_rate) }}</td>
              </tr>
              <tr v-if="!report.variants.length"><td colspan="7" class="text-center text-ink-500 py-6">暂无数据</td></tr>
            </tbody>
          </table>
          <p class="text-xs text-ink-500 mt-2" v-if="!report.winner">
            每版发够 100 条以上才有统计意义，现在还不足以判断胜负
          </p>
        </div>

        <div class="mb-6">
          <div class="label">转化漏斗</div>
          <div class="space-y-2">
            <div v-for="s in report.funnel" :key="s.stage" class="flex items-center gap-3">
              <div class="w-24 text-xs text-ink-700">{{ s.stage }}</div>
              <div class="flex-1 bg-slate-100 rounded h-6">
                <div class="funnel-bar h-6" :style="{width: Math.max(s.rate_of_top,1)+'%'}"></div>
              </div>
              <div class="w-24 text-right text-xs text-ink-500">{{ s.value }} · {{ pct(s.rate_of_prev) }}</div>
            </div>
          </div>
        </div>

        <div v-if="Object.keys(report.skipped||{}).length">
          <div class="label">合规拦截明细</div>
          <div class="flex flex-wrap gap-2">
            <span v-for="(count, reason) in report.skipped" :key="reason"
              class="tag bg-amber-50 text-amber-700">
              {{ {blacklist:'黑名单/已退订', not_mobile:'非手机号', too_frequent:'触达过频',
                  monthly_cap:'月度上限', duplicate_in_campaign:'活动内重复', no_phone:'无号码'}[reason] || reason }}
              {{ count }}
            </span>
          </div>
          <p class="text-xs text-ink-500 mt-2">拦截不是浪费，是在保护通道和品牌。投诉率超标会被运营商直接封停</p>
        </div>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 触达记录 / 回复 / 黑名单
// ---------------------------------------------------------------------------
const Records = {
  setup() {
    const tab = ref('messages');
    const messages = ref({ items: [], total: 0 });
    const replies = ref({ items: [], total: 0 });
    const blacklist = ref({ items: [], total: 0 });
    const filter = reactive({ status: '', page: 1 });
    const blacklistInput = ref('');

    async function load() {
      try {
        if (tab.value === 'messages') {
          const qs = new URLSearchParams({ page: filter.page, page_size: 20,
            ...(filter.status ? { status: filter.status } : {}) }).toString();
          messages.value = await get(`/api/sms/messages?${qs}`);
        } else if (tab.value === 'replies') {
          replies.value = await get('/api/sms/replies?page_size=50');
        } else {
          blacklist.value = await get('/api/sms/blacklist?page_size=50');
        }
      } catch (e) { notify(e.message, 'err'); }
    }
    watch(tab, () => { filter.page = 1; load(); });
    watch(() => filter.status, () => { filter.page = 1; load(); });
    onMounted(load);

    async function addBlacklist() {
      const phones = blacklistInput.value.split(/[,，\n\s]+/).map(s => s.trim()).filter(Boolean);
      if (!phones.length) return;
      try {
        const r = await post('/api/sms/blacklist', { phones, reason: 'manual' });
        notify(`已加入 ${r.added} 个号码`);
        blacklistInput.value = '';
        load();
      } catch (e) { notify(e.message, 'err'); }
    }
    async function handleReply(id) {
      try { await post(`/api/sms/replies/${id}/handle`); load(); } catch (e) { notify(e.message, 'err'); }
    }

    const INTENT_TEXT = { unsubscribe: '退订', interested: '有意向', complaint: '投诉', unknown: '待判断' };
    const INTENT_STYLE = { unsubscribe: 'bg-slate-100 text-slate-600',
      interested: 'bg-emerald-50 text-emerald-700', complaint: 'bg-rose-50 text-rose-600',
      unknown: 'bg-slate-100 text-slate-500' };

    return { tab, messages, replies, blacklist, filter, blacklistInput, load,
      addBlacklist, handleReply, fmtTime, STATUS_STYLE, STATUS_TEXT, INTENT_TEXT, INTENT_STYLE };
  },
  template: `
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold">触达记录</h1>
      <p class="text-sm text-ink-500 mt-1">每一条短信的去向都可追溯，回复里的意向线索要当天流转给 BD</p>
    </div>

    <div class="flex gap-2">
      <button v-for="t in [{k:'messages',n:'发送记录'},{k:'replies',n:'商户回复'},{k:'blacklist',n:'退订黑名单'}]"
        :key="t.k" @click="tab=t.k"
        :class="['btn', tab===t.k ? 'btn-primary' : 'btn-ghost']">{{ t.n }}</button>
    </div>

    <div v-if="tab==='messages'" class="card">
      <div class="px-5 py-3 border-b border-slate-100 flex gap-2">
        <select v-model="filter.status" class="select w-36">
          <option value="">全部状态</option>
          <option value="pending">待发</option>
          <option value="sent">已提交</option>
          <option value="delivered">已送达</option>
          <option value="failed">失败</option>
          <option value="skipped">合规拦截</option>
        </select>
      </div>
      <div class="overflow-x-auto">
        <table class="data">
          <thead><tr><th>号码</th><th>内容</th><th>状态</th><th>版本/轮次</th><th>计费</th><th>发送时间</th><th>点击</th></tr></thead>
          <tbody>
            <tr v-for="m in messages.items" :key="m.id">
              <td class="text-xs font-mono">{{ m.phone }}</td>
              <td class="text-xs max-w-md">{{ m.content || '—' }}</td>
              <td>
                <span class="tag" :class="STATUS_STYLE[m.status]">{{ STATUS_TEXT[m.status] }}</span>
                <div class="text-xs text-amber-600 mt-1" v-if="m.skip_reason">{{ m.skip_reason }}</div>
                <div class="text-xs text-rose-600 mt-1" v-if="m.error">{{ m.error }}</div>
              </td>
              <td class="text-xs">{{ m.variant }} / 第{{ m.round_no }}轮</td>
              <td class="text-xs">{{ m.fee_count }} 条</td>
              <td class="text-xs text-ink-500">{{ fmtTime(m.sent_at) }}</td>
              <td class="text-xs">
                <span v-if="m.clicked_at" class="text-emerald-600">已点击</span>
                <span v-else class="text-ink-500">—</span>
              </td>
            </tr>
            <tr v-if="!messages.items.length"><td colspan="7" class="text-center text-ink-500 py-12">暂无记录</td></tr>
          </tbody>
        </table>
      </div>
      <div class="flex items-center justify-between px-5 py-3 border-t border-slate-100 text-sm">
        <span class="text-ink-500">共 {{ messages.total }} 条</span>
        <div class="flex gap-2">
          <button class="btn btn-ghost btn-sm" :disabled="filter.page<=1" @click="filter.page--; load()">上一页</button>
          <button class="btn btn-ghost btn-sm" @click="filter.page++; load()">下一页</button>
        </div>
      </div>
    </div>

    <div v-if="tab==='replies'" class="card">
      <table class="data">
        <thead><tr><th>号码</th><th>回复内容</th><th>意图</th><th>时间</th><th>处理</th></tr></thead>
        <tbody>
          <tr v-for="r in replies.items" :key="r.id">
            <td class="text-xs font-mono">{{ r.phone }}</td>
            <td class="text-sm">{{ r.content }}</td>
            <td><span class="tag" :class="INTENT_STYLE[r.intent]">{{ INTENT_TEXT[r.intent] }}</span></td>
            <td class="text-xs text-ink-500">{{ fmtTime(r.received_at) }}</td>
            <td>
              <button v-if="!r.handled" class="btn btn-ghost btn-sm" @click="handleReply(r.id)">标记已跟进</button>
              <span v-else class="text-xs text-emerald-600">已跟进</span>
            </td>
          </tr>
          <tr v-if="!replies.items.length"><td colspan="5" class="text-center text-ink-500 py-12">
            暂无回复。需在创蓝控制台把上行回复推送地址配到 /callback/sms/reply</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab==='blacklist'" class="space-y-4">
      <div class="card p-5">
        <label class="label">手工加入黑名单（多个号码用换行或逗号分隔）</label>
        <div class="flex gap-2">
          <textarea v-model="blacklistInput" rows="2" class="textarea flex-1"
            placeholder="13800138000&#10;13900139000"></textarea>
          <button class="btn btn-primary self-end" @click="addBlacklist">加入</button>
        </div>
        <p class="text-xs text-ink-500 mt-2">
          商户回复 T/TD/退订 会自动进入黑名单且不可移出，这是合规底线
        </p>
      </div>
      <div class="card">
        <table class="data">
          <thead><tr><th>号码</th><th>原因</th><th>来源</th><th>加入时间</th></tr></thead>
          <tbody>
            <tr v-for="b in blacklist.items" :key="b.id">
              <td class="text-xs font-mono">{{ b.phone }}</td>
              <td class="text-xs">{{ {unsubscribe:'主动退订', complaint:'投诉', manual:'手工添加',
                invalid:'空号'}[b.reason] || b.reason }}</td>
              <td class="text-xs text-ink-500">{{ b.source }}</td>
              <td class="text-xs text-ink-500">{{ fmtTime(b.created_at) }}</td>
            </tr>
            <tr v-if="!blacklist.items.length"><td colspan="4" class="text-center text-ink-500 py-12">黑名单为空</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 通道设置
// ---------------------------------------------------------------------------
const Channel = {
  setup() {
    const status = ref({});
    const amap = ref({});
    const test = reactive({ phone: '', content: '【货袋子】测试短信，收到请忽略。回T退订' });
    const sending = ref(false);
    const tokenInput = ref(token.value);

    async function load() {
      try { status.value = await get('/api/sms/channel'); } catch (e) {}
      try { amap.value = await get('/api/collect/health'); } catch (e) {}
    }
    async function doTest() {
      if (!test.phone) return notify('请填写手机号', 'err');
      sending.value = true;
      try {
        const r = await post('/api/sms/test-send', { phone: test.phone, content: test.content });
        notify(r.ok ? (r.dry_run ? '演练模式：已记录但未真实下发' : '发送成功，请查收') : `失败：${r.error}`,
          r.ok ? 'ok' : 'err');
      } catch (e) { notify(e.message, 'err'); } finally { sending.value = false; }
    }
    function saveToken() {
      token.value = tokenInput.value;
      localStorage.setItem('hdz_token', tokenInput.value);
      notify('令牌已保存');
      load();
    }
    onMounted(load);
    return { status, amap, test, sending, tokenInput, load, doTest, saveToken, fmtTime,
      origin: window.location.origin };
  },
  template: `
  <div class="space-y-5">
    <div>
      <h1 class="text-xl font-semibold">通道与设置</h1>
      <p class="text-sm text-ink-500 mt-1">上线前把这一页的每一项都确认为绿色，再去跑真实活动</p>
    </div>

    <div class="grid md:grid-cols-2 gap-5">
      <div class="card p-5">
        <h2 class="font-medium mb-4">短信通道（创蓝 253）</h2>
        <div class="space-y-3 text-sm">
          <div class="flex justify-between items-center">
            <span class="text-ink-500">账号配置</span>
            <span :class="status.configured ? 'text-emerald-600' : 'text-rose-600'">
              {{ status.configured ? '已配置' : '未配置，请填 .env' }}</span>
          </div>
          <div class="flex justify-between items-center">
            <span class="text-ink-500">运行模式</span>
            <span :class="status.dry_run ? 'text-amber-600' : 'text-emerald-600'">
              {{ status.dry_run ? '演练模式（不真实下发）' : '正式发送' }}</span>
          </div>
          <div class="flex justify-between"><span class="text-ink-500">短信签名</span>
            <span>{{ status.sign }}</span></div>
          <div class="flex justify-between"><span class="text-ink-500">账户余额</span>
            <span>{{ status.balance?.balance ?? status.balance?.errorMsg ?? '-' }}</span></div>
          <div class="flex justify-between"><span class="text-ink-500">发送时间窗</span>
            <span>{{ status.send_window }}
              <span :class="status.in_window ? 'text-emerald-600' : 'text-amber-600'">
                （{{ status.in_window ? '当前可发' : '当前静默中' }}）</span></span></div>
          <div class="flex justify-between"><span class="text-ink-500">频控策略</span>
            <span>{{ status.min_interval_days }} 天内不重发 · 月上限 {{ status.max_touch_per_month }} 次</span></div>
          <div class="flex justify-between"><span class="text-ink-500">发送调度器</span>
            <span :class="status.dispatcher_running ? 'text-emerald-600' : 'text-rose-600'">
              {{ status.dispatcher_running ? '运行中' : '未运行' }}
              <span class="text-xs text-ink-500">{{ fmtTime(status.dispatcher_last_tick) }}</span></span></div>
        </div>
        <button class="btn btn-ghost w-full mt-4" @click="load">刷新状态</button>
      </div>

      <div class="card p-5">
        <h2 class="font-medium mb-4">高德接口</h2>
        <div class="flex justify-between text-sm mb-4">
          <span class="text-ink-500">连通状态</span>
          <span :class="amap.ok ? 'text-emerald-600' : 'text-rose-600'">
            {{ amap.ok ? '正常' : (amap.message || '异常') }}</span>
        </div>
        <div class="bg-slate-50 rounded-lg p-3 text-xs text-ink-500 leading-relaxed">
          Key 必须在高德控制台选择「Web服务」平台，选成 JS API 会报 10009。
          个人开发者默认 QPS 3、日调用量 3000，跑网格化采集前先确认配额够用。
        </div>

        <h2 class="font-medium mt-6 mb-3">访问令牌</h2>
        <div class="flex gap-2">
          <input v-model="tokenInput" class="input flex-1" placeholder="ADMIN_TOKEN" />
          <button class="btn btn-primary" @click="saveToken">保存</button>
        </div>
        <p class="text-xs text-ink-500 mt-2">与 .env 中的 ADMIN_TOKEN 一致，保存在本地浏览器</p>
      </div>
    </div>

    <div class="card p-5">
      <h2 class="font-medium mb-1">测试发送</h2>
      <p class="text-xs text-ink-500 mb-4">
        正式群发前，务必先发一条到自己手机，确认签名、错别字、链接跳转都没问题
      </p>
      <div class="grid md:grid-cols-3 gap-3">
        <input v-model="test.phone" class="input" placeholder="接收手机号" />
        <input v-model="test.content" class="input md:col-span-2" />
      </div>
      <button class="btn btn-primary mt-3" @click="doTest" :disabled="sending">
        {{ sending ? '发送中...' : '发送测试短信' }}
      </button>
    </div>

    <div class="card p-5">
      <h2 class="font-medium mb-3">回调地址（配置到创蓝控制台）</h2>
      <div class="space-y-2 text-sm font-mono bg-slate-50 rounded-lg p-4">
        <div><span class="text-ink-500 font-sans mr-2">状态报告推送</span>{{ origin }}/callback/sms/report</div>
        <div><span class="text-ink-500 font-sans mr-2">上行回复推送</span>{{ origin }}/callback/sms/reply</div>
        <div><span class="text-ink-500 font-sans mr-2">主站转化回传</span>{{ origin }}/callback/conversion</div>
      </div>
      <p class="text-xs text-ink-500 mt-3">
        不配上行回复推送，退订就无法自动生效，这是合规风险最高的一项，务必配置
      </p>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 宣传物料：行情海报 + 视频号成片
// ---------------------------------------------------------------------------
const Media = {
  setup() {
    const form = ref(null);
    const themes = ref([]);
    const jobs = ref([]);
    const kind = ref('video');
    const theme = ref('steel');
    const files = reactive({ video: null, bgm: null, channel_qr: null, qr: null, avatar: null });
    const submitting = ref(false);
    const active = ref(null);
    let timer = null;

    const emptyPrice = () => ({ name: '', price: '', change: 0, week: 0, lead: false });
    const emptyRes = () => ({ name: '', spec: '', origin: '', warehouse: '', qty: '', price: '', hot: false, tagsText: '' });

    async function load() {
      try {
        const [example, ts, js] = await Promise.all([
          get('/api/media/example'), get('/api/media/themes'), get('/api/media/jobs')]);
        const saved = localStorage.getItem('hdz_media_form');
        form.value = saved ? JSON.parse(saved) : normalize(example);
        themes.value = ts;
        jobs.value = js;
        if (js.some((j) => ['queued', 'running'].includes(j.status))) poll();
      } catch (e) { notify(e.message, 'err'); }
    }
    function normalize(d) {
      return {
        ...d,
        resources: (d.resources || []).map((r) => ({ ...r, tagsText: (r.tags || []).join(' ') })),
      };
    }
    function reset() {
      localStorage.removeItem('hdz_media_form');
      load();
      notify('已恢复样例数据');
    }
    function payload() {
      const f = form.value;
      const prices = f.prices.filter((p) => p.name && p.price !== '')
        .map((p) => ({ ...p, price: Number(p.price), change: Number(p.change || 0), week: Number(p.week || 0) }));
      const resources = f.resources.filter((r) => r.name)
        .map(({ tagsText, ...r }) => ({ ...r, price: Number(r.price), tags: tagsText.split(/[\s,，]+/).filter(Boolean) }));
      return { ...f, prices, resources, theme: theme.value };
    }
    function pick(key, ev) { files[key] = ev.target.files[0] || null; }
    async function submit() {
      const data = payload();
      if (!data.prices.length) return notify('至少填一行行情价格', 'err');
      localStorage.setItem('hdz_media_form', JSON.stringify(form.value));
      submitting.value = true;
      try {
        const fd = new FormData();
        fd.append('kind', kind.value);
        fd.append('theme', theme.value);
        fd.append('data', JSON.stringify(data));
        Object.entries(files).forEach(([k, v]) => v && fd.append(k, v));
        const res = await fetch('/api/media/jobs', { method: 'POST', body: fd, headers: { 'X-Api-Token': token.value } });
        const job = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(job.detail || `请求失败 ${res.status}`);
        jobs.value.unshift(job);
        active.value = job;
        notify(kind.value === 'video' ? '已开始出片，约 1 分钟' : '已开始渲染海报');
        poll();
      } catch (e) { notify(e.message, 'err'); } finally { submitting.value = false; }
    }
    function poll() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          jobs.value = await get('/api/media/jobs');
          if (active.value) active.value = jobs.value.find((j) => j.id === active.value.id) || active.value;
          if (jobs.value.some((j) => ['queued', 'running'].includes(j.status))) poll();
          else if (active.value?.status === 'done') notify('生成完成');
        } catch (e) { /* 轮询失败静默 */ }
      }, 2000);
    }
    const fileUrl = (job, name) => `/api/media/jobs/${job.id}/${name}?token=${encodeURIComponent(token.value)}`;
    const STATUS = { queued: '排队中', running: '生成中', done: '已完成', failed: '失败' };
    onMounted(load);
    onUnmounted(() => clearTimeout(timer));
    return { form, themes, jobs, kind, theme, files, submitting, active, emptyPrice, emptyRes,
      reset, pick, submit, fileUrl, STATUS, fmtTime };
  },
  template: `
  <div class="space-y-5" v-if="form">
    <div class="flex items-start justify-between">
      <div>
        <h1 class="text-xl font-semibold">宣传物料</h1>
        <p class="text-sm text-ink-500 mt-1">同一份行情数据，一键出静态海报和视频号成片。视频号分享进群，点开即看，评论区、主页都能直接跳到货袋子</p>
      </div>
      <button class="btn btn-ghost" @click="reset">恢复样例</button>
    </div>

    <div class="grid lg:grid-cols-[1fr_420px] gap-5">
      <div class="space-y-5">
        <div class="card p-5">
          <h2 class="font-medium mb-4">头部与结论</h2>
          <div class="grid md:grid-cols-3 gap-3 text-sm">
            <label class="block"><span class="text-ink-500 text-xs">品牌</span><input v-model="form.brand" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">期数</span><input v-model="form.issue_no" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">日期</span><input v-model="form.date" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">市场</span><input v-model="form.market" class="input mt-1" /></label>
            <label class="block md:col-span-2"><span class="text-ink-500 text-xs">副品牌 / 公司</span><input v-model="form.brand_sub" class="input mt-1" /></label>
            <label class="block md:col-span-3"><span class="text-ink-500 text-xs">大标题（支持 &lt;span class="hl"&gt;+40&lt;/span&gt; 高亮、&lt;br/&gt; 换行）</span>
              <input v-model="form.headline" class="input mt-1" /></label>
            <label class="block md:col-span-3"><span class="text-ink-500 text-xs">一句话结论（支持 &lt;b&gt; 加粗）</span>
              <textarea v-model="form.summary" class="textarea mt-1" rows="2"></textarea></label>
            <label class="block md:col-span-3"><span class="text-ink-500 text-xs">今日看点</span>
              <textarea v-model="form.insight" class="textarea mt-1" rows="3"></textarea></label>
          </div>
        </div>

        <div class="card p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-medium">今日行情 <span class="text-xs text-ink-500 font-normal ml-2">最多 6 个品种，勾选"领涨"的会做主卡</span></h2>
            <button class="btn btn-ghost text-xs" @click="form.prices.push(emptyPrice())" :disabled="form.prices.length >= 6">+ 品种</button>
          </div>
          <table class="data">
            <thead><tr><th>品种</th><th>均价</th><th>日涨跌</th><th>周涨跌</th><th>领涨</th><th></th></tr></thead>
            <tbody>
              <tr v-for="(p, i) in form.prices" :key="i">
                <td><input v-model="p.name" class="input" placeholder="热卷" /></td>
                <td><input v-model="p.price" type="number" class="input w-28" /></td>
                <td><input v-model="p.change" type="number" class="input w-24" /></td>
                <td><input v-model="p.week" type="number" class="input w-24" /></td>
                <td class="text-center"><input type="checkbox" v-model="p.lead" /></td>
                <td><button class="btn btn-danger text-xs" @click="form.prices.splice(i, 1)">删</button></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="card p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="font-medium">优势现货 <span class="text-xs text-ink-500 font-normal ml-2">最多 4 条，价格低于上表均价会自动标"低于均价 N"</span></h2>
            <button class="btn btn-ghost text-xs" @click="form.resources.push(emptyRes())" :disabled="form.resources.length >= 4">+ 资源</button>
          </div>
          <div class="space-y-3">
            <div v-for="(r, i) in form.resources" :key="i" class="grid md:grid-cols-8 gap-2 text-sm items-center bg-slate-50 rounded-lg p-3">
              <input v-model="r.name" class="input" placeholder="品名(与行情同名)" />
              <input v-model="r.spec" class="input md:col-span-2" placeholder="规格 材质" />
              <input v-model="r.origin" class="input" placeholder="钢厂" />
              <input v-model="r.warehouse" class="input" placeholder="仓库" />
              <input v-model="r.qty" class="input" placeholder="数量" />
              <input v-model="r.price" type="number" class="input" placeholder="价格" />
              <div class="flex items-center gap-2">
                <label class="text-xs whitespace-nowrap"><input type="checkbox" v-model="r.hot" /> 主推</label>
                <button class="btn btn-danger text-xs" @click="form.resources.splice(i, 1)">删</button>
              </div>
              <input v-model="r.tagsText" class="input md:col-span-8" placeholder="标签，空格分隔：一手货源 今日可提 可开13%票" />
            </div>
          </div>
          <div class="grid md:grid-cols-2 gap-3 mt-3 text-sm">
            <label class="block"><span class="text-ink-500 text-xs">现货备注</span><input v-model="form.res_note" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">更多现货提示</span><input v-model="form.res_more" class="input mt-1" /></label>
          </div>
        </div>

        <div class="card p-5">
          <h2 class="font-medium mb-4">商家与转化</h2>
          <div class="grid md:grid-cols-3 gap-3 text-sm">
            <label class="block"><span class="text-ink-500 text-xs">姓名</span><input v-model="form.name" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">头衔</span><input v-model="form.role" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">电话</span><input v-model="form.tel" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">视频号名称</span><input v-model="form.channel_name" class="input mt-1" placeholder="视频号里显示的名字" /></label>
            <label class="block md:col-span-2"><span class="text-ink-500 text-xs">服务卖点（海报底栏 / 视频结尾）</span><input v-model="form.service_title" class="input mt-1" /></label>
            <label class="block"><span class="text-ink-500 text-xs">价格</span><input v-model="form.service_price" class="input mt-1" /></label>
            <label class="block md:col-span-2"><span class="text-ink-500 text-xs">价格单位说明</span><input v-model="form.service_unit" class="input mt-1" /></label>
          </div>
        </div>
      </div>

      <div class="space-y-5">
        <div class="card p-5 sticky top-5">
          <h2 class="font-medium mb-4">生成</h2>
          <div class="flex gap-2 mb-4">
            <button :class="['btn flex-1', kind==='video' ? 'btn-primary' : 'btn-ghost']" @click="kind='video'">视频号成片</button>
            <button :class="['btn flex-1', kind==='poster' ? 'btn-primary' : 'btn-ghost']" @click="kind='poster'">静态海报</button>
          </div>
          <label class="block text-sm mb-3"><span class="text-ink-500 text-xs">风格</span>
            <select v-model="theme" class="select mt-1 w-full">
              <option v-for="t in themes" :key="t.key" :value="t.key">{{ t.key }} · {{ t.desc.split('：')[0] }}</option>
            </select>
          </label>
          <div class="space-y-3 text-sm">
            <label class="block" v-if="kind==='video'"><span class="text-ink-500 text-xs">背景素材（自己拍的仓库/装车/现场视频，mp4/mov，≤200MB；不传用动态渐变底）</span>
              <input type="file" accept="video/*" class="mt-1 block w-full text-xs" @change="pick('video', $event)" /></label>
            <label class="block" v-if="kind==='video'"><span class="text-ink-500 text-xs">背景音乐（可选，mp3/m4a）</span>
              <input type="file" accept="audio/*" class="mt-1 block w-full text-xs" @change="pick('bgm', $event)" /></label>
            <label class="block" v-if="kind==='video'"><span class="text-ink-500 text-xs">视频号二维码（视频号助手 → 主页二维码，片尾和分享封面用）</span>
              <input type="file" accept="image/*" class="mt-1 block w-full text-xs" @change="pick('channel_qr', $event)" /></label>
            <label class="block" v-if="kind==='poster'"><span class="text-ink-500 text-xs">海报二维码（货袋子店铺码 / 视频号码）</span>
              <input type="file" accept="image/*" class="mt-1 block w-full text-xs" @change="pick('qr', $event)" /></label>
            <label class="block"><span class="text-ink-500 text-xs">头像（可选）</span>
              <input type="file" accept="image/*" class="mt-1 block w-full text-xs" @change="pick('avatar', $event)" /></label>
          </div>
          <button class="btn btn-primary w-full mt-4" @click="submit" :disabled="submitting">
            {{ submitting ? '上传中...' : (kind==='video' ? '生成视频号成片（约 1 分钟）' : '生成海报') }}
          </button>
          <div v-if="kind==='video'" class="text-xs text-ink-500 mt-3 leading-relaxed bg-slate-50 rounded-lg p-3">
            <b class="text-ink-700">发布三步</b><br/>
            1. 下载 video.mp4 与 cover.png，在视频号助手发布，封面选 cover.png，正文第一行放货袋子店铺链接<br/>
            2. 把视频号卡片分享到群，用户点开就是视频，头像和主页都能跳到货袋子<br/>
            3. 不方便发卡片的群，发 cover_share.png，长按二维码同样直达视频号
          </div>
        </div>
      </div>
    </div>

    <div class="card p-5">
      <h2 class="font-medium mb-3">生成记录 <span class="text-xs text-ink-500 font-normal ml-2">服务重启后列表清空，文件保留在 data/media/</span></h2>
      <div v-if="!jobs.length" class="text-sm text-ink-500">还没有生成过物料</div>
      <div v-else class="space-y-3">
        <div v-for="j in jobs" :key="j.id" class="border border-slate-100 rounded-xl p-4">
          <div class="flex items-center justify-between text-sm">
            <div class="flex items-center gap-3">
              <span class="font-mono text-xs text-ink-500">{{ j.id }}</span>
              <span class="tag">{{ j.kind === 'video' ? '视频' : '海报' }}</span>
              <span class="tag">{{ j.theme }}</span>
              <span v-if="j.has_video" class="tag">自传素材</span>
              <span :class="['tag', j.status==='done' && 'bg-emerald-50 text-emerald-700', j.status==='failed' && 'bg-rose-50 text-rose-700']">{{ STATUS[j.status] }}</span>
            </div>
            <span class="text-xs text-ink-500">{{ fmtTime(j.created_at) }}</span>
          </div>
          <div v-if="j.status==='failed'" class="text-xs text-rose-600 mt-2">{{ j.error }}</div>
          <div v-if="['queued','running'].includes(j.status)" class="text-xs text-ink-500 mt-2 font-mono">{{ j.logs.slice(-1)[0] || '等待中...' }}</div>
          <div v-if="j.status==='done'" class="mt-3 flex flex-wrap gap-4 items-start">
            <video v-if="j.files.video" :src="fileUrl(j, j.files.video)" controls class="h-64 rounded-lg bg-black"></video>
            <template v-for="(name, key) in j.files" :key="key">
              <a v-if="key !== 'video'" :href="fileUrl(j, name)" target="_blank" class="block">
                <img :src="fileUrl(j, name)" class="h-64 rounded-lg border border-slate-200" />
                <div class="text-xs text-center text-ink-500 mt-1">{{ name }}</div>
              </a>
            </template>
            <div class="text-xs space-y-2">
              <a v-for="(name, key) in j.files" :key="key" :href="fileUrl(j, name)" :download="name" class="btn btn-ghost block text-center">下载 {{ name }}</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>`,
};

// ---------------------------------------------------------------------------
// 主应用
// ---------------------------------------------------------------------------
const App = {
  components: { Dashboard, Collect, Merchants, Templates, Campaigns, Records, Channel, Media },
  setup() {
    const current = ref(localStorage.getItem('hdz_page') || 'Dashboard');
    const health = ref({});
    const menus = [
      { key: 'Dashboard', name: '增长概览', icon: '◎' },
      { key: 'Collect', name: '商机采集', icon: '⌕' },
      { key: 'Merchants', name: '商户库', icon: '▤' },
      { key: 'Templates', name: '短信模板', icon: '✎' },
      { key: 'Campaigns', name: '触达活动', icon: '➤' },
      { key: 'Records', name: '触达记录', icon: '☰' },
      { key: 'Media', name: '宣传物料', icon: '▶' },
      { key: 'Channel', name: '通道设置', icon: '⚙' },
    ];
    function go(key) {
      current.value = key;
      localStorage.setItem('hdz_page', key);
    }
    onMounted(async () => {
      try { health.value = await (await fetch('/health')).json(); } catch (e) {}
    });
    return { current, menus, go, toast, health, token };
  },
  template: `
  <div class="flex min-h-screen">
    <aside class="w-56 bg-white border-r border-slate-200 p-4 flex flex-col shrink-0">
      <div class="px-2 py-3 mb-4">
        <div class="text-lg font-semibold text-brand-600">货袋子</div>
        <div class="text-xs text-ink-500 mt-0.5">商户增长运营平台</div>
      </div>
      <nav class="space-y-1 flex-1">
        <div v-for="m in menus" :key="m.key" @click="go(m.key)"
          :class="['nav-item', current===m.key && 'active']">
          <span class="w-4 text-center">{{ m.icon }}</span>{{ m.name }}
        </div>
      </nav>
      <div class="text-xs text-ink-500 px-2 space-y-1 pt-4 border-t border-slate-100">
        <div v-if="health.dry_run" class="text-amber-600">● 短信演练模式</div>
        <div v-else class="text-emerald-600">● 正式发送模式</div>
        <div :class="health.amap_ready ? 'text-emerald-600' : 'text-rose-500'">
          ● 高德 {{ health.amap_ready ? '已配置' : '未配置' }}</div>
        <div class="pt-1">v{{ health.version || '1.0.0' }}</div>
      </div>
    </aside>

    <main class="flex-1 p-7 max-w-[1600px] overflow-x-hidden">
      <div v-if="!token" class="card p-4 mb-5 border-amber-200 bg-amber-50 text-sm text-amber-800">
        还没有设置访问令牌，接口会返回 401。请到「通道设置」填写 ADMIN_TOKEN
      </div>
      <component :is="current" />
    </main>

    <transition name="fade">
      <div v-if="toast.show" class="toast" :class="toast.type==='err' ? 'bg-rose-600' : 'bg-ink-900'">
        {{ toast.text }}
      </div>
    </transition>
  </div>`,
};

createApp(App).mount('#app');
