import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'
import AdminView from './AdminView.vue'
import KnowledgeAdmin from '@/components/KnowledgeAdmin.vue'

let pinia: Pinia
beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})

describe('AdminView', () => {
  it('渲染页面标题与 KnowledgeAdmin 组件', () => {
    const w = mount(AdminView, { global: { plugins: [pinia, ElementPlus] } })
    expect(w.text()).toContain('知识库管理')
    expect(w.findComponent(KnowledgeAdmin).exists()).toBe(true)
  })
})
