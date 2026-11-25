<script setup lang="ts">
import { computed, ref, watch } from "vue";
type Expense = {
  kind: string;
  quantity?: number | null;
  actual_amount?: string | null;
};
const props = defineProps<{ initial?: Expense; disabled?: boolean }>();
const emit = defineEmits<{ calculate: [value: Expense] }>();
const kind = ref("office"),
  quantity = ref<number>(),
  amount = ref("");
watch(
  () => props.initial,
  (value) => {
    if (value) {
      kind.value = value.kind;
      quantity.value = value.quantity ?? undefined;
      amount.value = value.actual_amount ?? "";
    }
  },
  { immediate: true, deep: true },
);
const valid = computed(
  () =>
    Number.isInteger(quantity.value) &&
    quantity.value! >= 1 &&
    quantity.value! <= 100 &&
    /^\d{1,6}(?:\.\d{1,2})?$/.test(amount.value),
);
</script>
<template>
  <div class="expense-form" data-testid="expense-confirmation">
    <div class="field">
      <label>费用类别</label
      ><el-select v-model="kind" aria-label="其他费用类别" :disabled="disabled">
        <el-option label="办公用品采购" value="office" /><el-option
          label="培训报销"
          value="training"
        /><el-option label="业务招待" value="hospitality" />
      </el-select>
    </div>
    <div class="field">
      <label>{{ kind === "office" ? "采购套数" : "参与人数" }}</label
      ><el-input-number
        :key="'expense-quantity-' + !!disabled"
        v-model="quantity"
        :min="1"
        :max="100"
        :precision="0"
        aria-label="费用数量"
        :disabled="disabled"
      />
    </div>
    <div class="field">
      <label>实际申请金额（元）</label
      ><el-input
        v-model="amount"
        inputmode="decimal"
        maxlength="9"
        aria-label="实际申请金额"
        placeholder="例如：6000.00"
        :disabled="disabled"
      />
    </div>
    <p class="subtle">
      核对数量和实际申请金额后确认；单位上限、比例和单次上限由当前有效规则提供。一次核算一种费用，结果不代表财务审批。
    </p>
    <el-button
      type="primary"
      :disabled="disabled || !valid"
      @click="emit('calculate', { kind, quantity, actual_amount: amount })"
      >确认条件并核算</el-button
    >
  </div>
</template>
