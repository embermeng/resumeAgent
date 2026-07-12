import {
  Callout,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  MetricsGrid,
  Stack,
  Table,
  Tag,
  Text,
  Timeline,
  Fluency,
  Row,
  Pill,
} from 'qoder/canvas';

const phaseData = [
  {
    id: 'phase1',
    name: '阶段1\n基础设施',
    score: 100,
    status: 'high' as const,
  },
  {
    id: 'phase2',
    name: '阶段2\n知识处理',
    score: 100,
    status: 'high' as const,
  },
  {
    id: 'phase3',
    name: '阶段3\n检索层',
    score: 100,
    status: 'high' as const,
  },
  {
    id: 'phase4',
    name: '阶段4\nAgent',
    score: 100,
    status: 'high' as const,
  },
  {
    id: 'phase5',
    name: '阶段5\nUI与集成',
    score: 100,
    status: 'high' as const,
  },
];

export default function ResumeAgentReport() {
  return (
    <Stack gap={20}>
      <H1>ResumeAgent - 项目完成报告</H1>
      <Text tone="secondary">
        基于 LangGraph + RAG 流水线构建的双模式路由智能简历生成 Agent。
        将课程 PDF 和项目源码转化为知识库，通过智能 Agent 实现技术问答与简历自动生成。
      </Text>

      <Divider />

      <MetricsGrid
        columns={5}
        items={[
          { label: '源文件数', value: '42', tone: 'info' },
          { label: '测试通过', value: '132', tone: 'success' },
          { label: '代码警告', value: '0', tone: 'success' },
          { label: '阶段完成', value: '5/5', tone: 'success' },
          { label: '测试耗时', value: '3.9s', tone: 'neutral' },
        ]}
      />

      <Divider />

      <Fluency
        title="实施进度"
        subtitle="全部 5 个阶段已完成"
        stages={phaseData}
        height={200}
        labels={{ project: 'ResumeAgent' }}
      />

      <Divider />

      <H2>架构分层</H2>
      <Table
        columns={[
          { key: 'layer', title: '层级', width: '120px' },
          { key: 'modules', title: '核心模块' },
          { key: 'tests', title: '测试数', width: '80px', align: 'right' },
          {
            key: 'status',
            title: '状态',
            width: '100px',
            render: () => <Tag tone="success" size="sm">已完成</Tag>,
          },
        ]}
        rows={[
          {
            layer: '基础设施',
            modules: 'config.py, api_client.py（DashScope / OpenAI / Gemini）',
            tests: '36',
            status: 'done',
          },
          {
            layer: '知识处理',
            modules: 'pdf_parser.py, text_splitter.py, ingestion.py, project_extractor.py',
            tests: '47',
            status: 'done',
          },
          {
            layer: '检索层',
            modules: 'retriever.py（BM25/向量/混合检索）, reranker.py（LLM重排序）',
            tests: '15',
            status: 'done',
          },
          {
            layer: 'Agent',
            modules: 'intent.py, tools.py, state.py, graph.py（LangGraph双模式路由）',
            tests: '34',
            status: 'done',
          },
          {
            layer: 'UI',
            modules: 'app_streamlit.py（Streamlit Web UI）, main.py（CLI 6个子命令）',
            tests: '-',
            status: 'done',
          },
        ]}
      />

      <Divider />

      <H2>Agent 路由架构</H2>
      <Grid columns={3} gap={16}>
        <Stack gap={8}>
          <Row gap={8} align="center">
            <Pill tone="info" size="sm">快速回答</Pill>
          </Row>
          <Text size="small">
            单次检索后直接回答。适用于技术问题、知识查询和项目信息查询。
          </Text>
          <Text tone="secondary" size="small">
            意图识别 → 检索 → 回答 → 回复
          </Text>
        </Stack>
        <Stack gap={8}>
          <Row gap={8} align="center">
            <Pill tone="success" size="sm">深度思考</Pill>
          </Row>
          <Text size="small">
            多步检索、简历草稿生成，可选针对岗位要求进行优化。
          </Text>
          <Text tone="secondary" size="small">
            意图识别 → 检索 ×2 → 草稿 → 优化 → 简历
          </Text>
        </Stack>
        <Stack gap={8}>
          <Row gap={8} align="center">
            <Pill tone="neutral" size="sm">闲聊兜底</Pill>
          </Row>
          <Text size="small">
            无法识别意图时的兜底模式，以 Agent 角色回复并引导用户提出具体需求。
          </Text>
          <Text tone="secondary" size="small">
            意图识别 → LLM回复 → 回复
          </Text>
        </Stack>
      </Grid>

      <Divider />

      <H2>技术亮点</H2>
      <Grid columns={2} gap={12}>
        <Stack gap={4}>
          <H3>LangGraph StateGraph</H3>
          <Text size="small">基于 LLM 意图分类 + 关键词降级的 3 路径条件路由</Text>
        </Stack>
        <Stack gap={4}>
          <H3>混合检索</H3>
          <Text size="small">BM25 + FAISS 向量检索 + LLM 重排序，加权融合评分</Text>
        </Stack>
        <Stack gap={4}>
          <H3>多模型支持</H3>
          <Text size="small">统一 APIProcessor 支持 DashScope、OpenAI、Gemini 三种后端</Text>
        </Stack>
        <Stack gap={4}>
          <H3>完整测试隔离</H3>
          <Text size="small">所有外部 API（LLM、Embedding、Docling）通过 pytest Mock 完全隔离</Text>
        </Stack>
      </Grid>

      <Divider />

      <H2>实施时间线</H2>
      <Timeline
        events={[
          {
            id: 'p1',
            timestamp: '阶段 1',
            title: '基础设施搭建',
            description: '项目骨架、config.py（19个测试）、api_client.py（17个测试）、requirements.txt、.env.example、.gitignore',
          },
          {
            id: 'p2',
            timestamp: '阶段 2',
            title: '知识处理模块',
            description: 'PDF解析器（8）、文本分块（14）、向量化入库（11）、项目提炼器（14）、main.py CLI',
          },
          {
            id: 'p3',
            timestamp: '阶段 3',
            title: '检索层',
            description: 'BM25/向量/混合检索器（7）、LLM重排序（8）',
          },
          {
            id: 'p4',
            timestamp: '阶段 4',
            title: 'Agent 层',
            description: 'Pydantic数据模型（16）、提示词模板、意图分类器、工具集、状态定义、LangGraph图（5）',
          },
          {
            id: 'p5',
            timestamp: '阶段 5',
            title: 'UI与集成',
            description: 'Streamlit Web UI、CLI聊天模式、端到端集成验证',
          },
          {
            id: 'audit',
            timestamp: '最终审计',
            title: '完成度验证',
            description: '修复 Pydantic V2 弃用警告，补建 .env.example 和 .gitignore，132个测试全部通过，0警告',
          },
        ]}
      />

      <Divider />

      <Callout type="success" title="全部阶段已完成">
        <Text size="small">
          42 个 Python 文件，132 个测试全部通过（耗时 3.9s），0 个代码警告。
          阶段 6（知识库初始化）需要用户放入真实数据——将 PDF 和项目源码放入 data/ 目录。
        </Text>
      </Callout>

      <Text tone="tertiary" size="small">
        项目路径：f:\study\AI大模型课\ResumeAgent\
      </Text>
    </Stack>
  );
}
