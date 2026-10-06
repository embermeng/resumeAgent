<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

/**
 * 登录 / 注册页(契约 4.11、4.12)。
 * 单页切换两种模式:登录用 email+password,注册多一个 username。
 * 成功后跳转 ?redirect= 指定的原目标(路由守卫拦截时带上),默认回对话主页。
 */
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mode = ref<'login' | 'register'>('login')
const formRef = ref<FormInstance>()
const errorMsg = ref('')

const form = reactive({
  username: '',
  email: '',
  password: '',
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 1, max: 50, message: '用户名 1~50 字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
}

function switchMode(next: 'login' | 'register') {
  mode.value = next
  errorMsg.value = ''
  formRef.value?.clearValidate()
}

async function onSubmit() {
  if (!formRef.value) return
  // 登录模式不校验 username 字段
  const fields = mode.value === 'login' ? ['email', 'password'] : ['username', 'email', 'password']
  const valid = await formRef.value.validateField(fields).then(() => true).catch(() => false)
  if (!valid) return

  errorMsg.value = ''
  try {
    if (mode.value === 'login') {
      await auth.login(form.email, form.password)
    } else {
      await auth.register(form.username, form.email, form.password)
    }
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect)
  } catch (e) {
    errorMsg.value = (e as Error).message || '操作失败'
    ElMessage.error(errorMsg.value)
  }
}
</script>

<template>
  <div class="login-view">
    <div class="login-shell">
      <!-- 品牌展示区:纯装饰,不含任何表单控件(保证登录模式仅 2 个 input) -->
      <div class="brand-panel">
        <div class="brand-logo">📄</div>
        <h1 class="brand-name">ResumeAgent</h1>
        <p class="brand-desc">基于 LangGraph + RAG 的智能简历生成助手</p>
        <ul class="brand-points">
          <li>💡 课程知识智能问答</li>
          <li>🗂 项目素材自动组装</li>
          <li>📝 一键生成与增强简历</li>
        </ul>
      </div>

      <el-card class="login-card" shadow="always">
        <h2 class="title">{{ mode === 'login' ? '登录' : '注册' }}</h2>

        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
          <el-form-item v-if="mode === 'register'" label="用户名" prop="username">
            <el-input v-model="form.username" placeholder="1~50 字符" maxlength="50" />
          </el-form-item>
          <el-form-item label="邮箱" prop="email">
            <el-input v-model="form.email" type="email" placeholder="you@example.com" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input v-model="form.password" type="password" show-password placeholder="至少 8 位" />
          </el-form-item>

          <el-alert v-if="errorMsg" :title="errorMsg" type="error" show-icon :closable="false" class="err" />

          <el-button type="primary" class="submit" native-type="submit" :loading="auth.loading">
            {{ mode === 'login' ? '登录' : '注册并登录' }}
          </el-button>
        </el-form>

        <div class="switch">
          <template v-if="mode === 'login'">
            还没有账号?
            <el-link type="primary" @click="switchMode('register')">去注册</el-link>
          </template>
          <template v-else>
            已有账号?
            <el-link type="primary" @click="switchMode('login')">去登录</el-link>
          </template>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(1200px 600px at 15% 20%, rgba(99, 102, 241, 0.18), transparent 60%),
    radial-gradient(1000px 500px at 85% 80%, rgba(139, 92, 246, 0.16), transparent 60%),
    var(--rp-page-bg);
}
.login-shell {
  display: flex;
  align-items: stretch;
  width: min(860px, 100%);
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.14);
}
/* 品牌展示区 */
.brand-panel {
  flex: 1 1 46%;
  padding: 44px 36px;
  color: #fff;
  background: linear-gradient(150deg, var(--rp-brand-from), var(--rp-brand-to));
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
}
.brand-logo {
  font-size: 40px;
  filter: drop-shadow(0 4px 10px rgba(0, 0, 0, 0.2));
}
.brand-name {
  margin: 8px 0 4px;
  font-size: 30px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.brand-desc {
  margin: 0 0 22px;
  font-size: 14px;
  opacity: 0.92;
  line-height: 1.6;
}
.brand-points {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
}
.brand-points li {
  padding-left: 4px;
  opacity: 0.96;
}
/* 表单卡片 */
.login-card {
  flex: 1 1 54%;
  border: none;
  border-radius: 0;
}
.login-card :deep(.el-card__body) {
  padding: 40px 36px;
}
.title {
  margin: 0 0 24px;
  text-align: center;
  font-size: 22px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}
.err {
  margin-bottom: 12px;
}
.submit {
  width: 100%;
  height: 42px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 10px;
}
.switch {
  margin-top: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
/* 窄屏隐藏品牌区,只留表单 */
@media (max-width: 720px) {
  .brand-panel {
    display: none;
  }
  .login-shell {
    width: 100%;
    max-width: 400px;
    border-radius: 16px;
  }
}
</style>
