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
  modelDefaultOption: '默认模型',
  duration: '时长',
  generate: '生成',
  submitting: '已提交…',
  notLoggedIn: '尚未登录咕咕聚合网关',
  goLogin: '去登录',
  saving: '保存中…',
  saved: '已保存',
  // 画布工具栏（M2b）
  toolSelect: '选择/移动',
  toolHand: '拖拽画布（或按住空格）',
  toolPoint: '点注释',
  toolRect: '框选注释',
  toolLock: '锁定/解锁',
  toolForward: '置于顶层',
  toolFit: '适应视图',
  toolUpload: '导入素材',
  toolHide: '隐藏所选（可撤销找回）',
  hintPoint: '点击图片放置注释点 · 再点工具按钮退出',
  hintRect: '在图片上拖出注释区域 · 再点工具按钮退出',
  aiEntry: '打开 AI 输入框（⌘⌥A）',
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
  modelDefaultOption: 'Default model',
  duration: 'Duration',
  generate: 'Generate',
  submitting: 'Submitted…',
  notLoggedIn: 'Not signed in to the gugu gateway',
  goLogin: 'Sign in',
  saving: 'Saving…',
  saved: 'Saved',
  // 画布工具栏（M2b）
  toolSelect: 'Select / move',
  toolHand: 'Pan canvas (or hold Space)',
  toolPoint: 'Point comment',
  toolRect: 'Rect comment',
  toolLock: 'Lock / unlock',
  toolForward: 'Bring to front',
  toolFit: 'Fit to view',
  toolUpload: 'Import assets',
  toolHide: 'Hide selection (undoable)',
  hintPoint: 'Click an image to drop a comment · click the tool again to exit',
  hintRect: 'Drag a comment region on an image · click the tool again to exit',
  aiEntry: 'Open AI composer (Mod+Alt+A)',
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
