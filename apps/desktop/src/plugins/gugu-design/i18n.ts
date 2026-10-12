/** 轻量文案（zh/en）。桌面语言环境为 zh-CN 时取中文。 */
const zh: Record<string, string> = {
  title: '咕咕设计',
  newProjectPlaceholder: '新工程名，如「秋季新品短视频」',
  create: '新建',
  loading: '加载中…',
  noProjects: '还没有工程 —— 新建一个开始创作',
  assetCount: '{n} 个素材',
  confirmDelete: '删除工程「{name}」？其本地文件将一并移除。',
  tabImage: '图像',
  tabVideo: '视频',
  promptPlaceholder: '描述你想生成的画面…',
  modelPlaceholder: '模型（留空用默认）',
  duration: '时长',
  generate: '生成',
  submitting: '已提交…',
  notLoggedIn: '尚未登录咕咕聚合网关',
  goLogin: '去登录',
  saving: '保存中…',
  saved: '已保存',
  'job.queued': '排队',
  'job.running': '生成中',
  'job.succeeded': '完成',
  'job.failed': '失败'
}

const en: Record<string, string> = {
  title: 'Gugu Design',
  newProjectPlaceholder: 'New project name…',
  create: 'Create',
  loading: 'Loading…',
  noProjects: 'No projects yet — create one to start',
  assetCount: '{n} assets',
  confirmDelete: 'Delete project “{name}”? Its local files will be removed.',
  tabImage: 'Image',
  tabVideo: 'Video',
  promptPlaceholder: 'Describe the scene…',
  modelPlaceholder: 'Model (empty = default)',
  duration: 'Duration',
  generate: 'Generate',
  submitting: 'Submitted…',
  notLoggedIn: 'Not signed in to the gugu gateway',
  goLogin: 'Sign in',
  saving: 'Saving…',
  saved: 'Saved',
  'job.queued': 'Queued',
  'job.running': 'Running',
  'job.succeeded': 'Done',
  'job.failed': 'Failed'
}

const dict = (navigator.language || 'zh').toLowerCase().startsWith('zh') ? zh : en

export function t(key: string, params?: Record<string, string | number>): string {
  let s = dict[key] ?? key

  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replaceAll(`{${k}}`, String(v))
    }
  }

  return s
}
