/**
 * Human-readable labels and remediation hints for the LLM security
 * categories.  The backend only ever sends a closed vocabulary
 * (`SECURITY_CATEGORIES` in security_scanner.py), never the model's free-text
 * reason, so this map is the only place where wording is added.
 */
export type SecurityCategoryInfo = {
  label: string;
  advice: string;
};

const FALLBACK: SecurityCategoryInfo = {
  label: "其他风险",
  advice: "请检查该文件，确认是否存在可疑的指令或数据处理。",
};

export const SECURITY_CATEGORY_INFO: Record<string, SecurityCategoryInfo> = {
  hardcoded_credential: {
    label: "硬编码凭证",
    advice: "把密钥改为从环境变量读取，并删除文件中的明文值。",
  },
  exfiltration: {
    label: "数据外送",
    advice: "确认外部上传目标是预期的，避免把本地数据或凭证发送到未授权地址。",
  },
  prompt_injection: {
    label: "提示注入",
    advice: "删除要求模型忽略既有指令、或隐藏/延迟执行指令的文本。",
  },
  system_override: {
    label: "系统角色覆盖",
    advice: "删除试图改写系统提示或角色设定的内容。",
  },
  privilege_escalation: {
    label: "提权操作",
    advice: "移除 sudo、chown 等需要更高权限的步骤，或说明由管理员执行。",
  },
  unsafe_code: {
    label: "不安全代码",
    advice: "移除“下载后直接执行”和动态 eval 等不可审计的执行路径。",
  },
  borderline_api: {
    label: "外部 API 引用",
    advice: "确认该外部 API 属于业务必需，并在文档中说明用途。",
  },
  other: FALLBACK,
};

export function securityCategoryInfo(
  category?: string | null,
): SecurityCategoryInfo | null {
  if (!category) {
    return null;
  }
  return SECURITY_CATEGORY_INFO[category] ?? FALLBACK;
}
