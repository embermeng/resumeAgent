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
    <el-card class="login-card" shadow="always">
      <div class="brand">📄 ResumeAgent</div>
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
</template>

<style scoped>
.login-view {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--el-fill-color-light);
}
.login-card {
  width: 380px;
}
.brand {
  font-weight: 700;
  font-size: 18px;
  text-align: center;
}
.title {
  margin: 8px 0 20px;
  text-align: center;
  font-size: 20px;
}
.err {
  margin-bottom: 12px;
}
.submit {
  width: 100%;
}
.switch {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
