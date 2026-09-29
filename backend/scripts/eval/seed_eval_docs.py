# -*- coding: utf-8 -*-
"""
测评知识库注入：生成 4 份业务测试文档并走真实上传链路入库
=========================================================
- 文档内容与 eval/dataset.json 的 ground_truth_contexts 对齐
- 文件名带 [EVAL] 前缀，测评后可清理（见 cleanup_eval_docs.py）
- 使用独立测试用户（不污染 admin 数据）

用法：
    python seed_eval_docs.py            # 生成 + 上传
    python seed_eval_docs.py --dry-run  # 只生成文档，不上传
"""

import argparse
import os
import time
import uuid

import requests

BACKEND = r"D:\study\ai_study\proj\ai-workbench\backend"
DOCS_DIR = os.path.join(BACKEND, "scripts", "eval", "test_docs")
API = "http://127.0.0.1:8000"

# 与 dataset.json ground_truth 对齐的测试文档（[EVAL] 标记，便于清理）
TEST_DOCS = {
    "售后政策[EVAL].md": """# 售后政策

## 第一章 总则

本政策适用于本公司销售的所有商品。如与国家法律法规冲突，以法律法规为准。

## 第二章 退货与换货

### 第1条 退货条件

商品需保持完好，不影响二次销售。

### 第2条 无理由退货

自购买之日起7天内无理由退货，15天内可换货。

### 第3条 特殊商品

定制类商品、已拆封的影音制品不支持无理由退货。

## 第三章 退款流程

退货审核通过后，退款将在3个工作日内原路退回。

### 联系方式

客服电话：400-000-0000（工作日 9:00-18:00）。
""",
    "隐私协议[EVAL].md": """# 隐私协议

## 第一章 总则

本协议说明我们如何收集、使用和保护您的个人信息。

## 第二章 数据收集

我们仅收集提供服务所必需的个人信息，包括账户信息、订单信息、设备信息。

## 第三章 数据使用

收集的数据仅用于订单履约、客户服务与安全风控。

### 第3.5条 共享限制

未经您的授权，我们不会将您的个人信息提供给任何无关第三方。

## 第四章 数据保护

### 第4.1条 数据隐私承诺

我们承诺不向任何第三方出售用户数据。所有数据传输均使用AES-256加密。

### 第4.2条 存储期限

数据存储期限不超过服务终止后6个月。

## 第五章 用户权利

您有权查询、更正、删除您的个人信息，可通过客服渠道行使上述权利。
""",
    "员工手册[EVAL].md": """# 员工手册

## 第一章 入职与离职

新员工入职需签订劳动合同，试用期不超过6个月。

## 第二章 考勤管理

### 第2.1条 考勤时间

员工需在上午9点前打卡，下班时间为下午6点。

### 第2.2条 迟到处理

迟到超过30分钟扣半天工资，当月累计迟到3次以上给予书面警告。

### 第2.3条 补卡规则

每月允许3次补卡机会，补卡需在当月内完成。

### 第2.4条 请假制度

事假需提前1个工作日申请，年假需提前3个工作日申请。

## 第三章 薪酬福利

工资于每月10日发放，含五险一金与年终奖。

## 第四章 行为规范

员工需遵守保密制度，不得泄露公司商业机密。
""",
    "API开发文档[EVAL].md": """# API 开发文档

## 第一章 概述

本 API 提供数据查询与业务操作能力，支持 RESTful 风格调用。

## 第二章 认证方式

使用 API Key 进行认证，请求头需携带 `Authorization: Bearer <API_KEY>`。

## 第三章 频率限制

### 第3.1条 免费版限制

免费版限制 60次/分钟，超出后请求返回 429 状态码。

### 第3.2条 企业版限制

企业版限制 600次/分钟，可联系商务开通更高配额。

### 第3.3条 超限处理

被限流后请等待窗口重置后重试，建议实现指数退避。

## 第四章 错误码

- 400：参数错误
- 401：认证失败
- 404：资源不存在
- 429：请求过于频繁
- 500：服务端错误
""",
}


def gen_docs(dry_run: bool):
    os.makedirs(DOCS_DIR, exist_ok=True)
    paths = []
    for name, content in TEST_DOCS.items():
        p = os.path.join(DOCS_DIR, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        paths.append(p)
        print(f"生成: {p} ({len(content)} 字)")
    return paths


def upload(api: str, token: str, filepath: str) -> dict:
    with open(filepath, "rb") as f:
        files = {"file": (os.path.basename(filepath), f, "text/markdown")}
        r = requests.post(
            f"{api}/api/documents/upload", headers={"Authorization": f"Bearer {token}"}, files=files, timeout=120
        )
    try:
        return r.json()
    except Exception:
        return {"status": "error", "raw": r.text[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    paths = gen_docs(args.dry_run)
    if args.dry_run:
        print("\n[dry-run] 未上传，仅生成文档")
        return

    # 独立测试用户
    u = f"eval_{uuid.uuid4().hex[:8]}"
    r = requests.post(f"{API}/api/auth/register", json={"username": u, "password": "Eval123456!"}, timeout=10)
    if r.status_code == 200:
        token = r.json().get("token")
    else:
        r = requests.post(f"{API}/api/auth/login", json={"username": u, "password": "Eval123456!"}, timeout=10)
        token = r.json().get("token")
    print(f"测试用户: {u} (token {'OK' if token else 'FAIL'})")

    results = {}
    for p in paths:
        res = upload(API, token, p)
        results[os.path.basename(p)] = res
        print(f"上传 {os.path.basename(p)}: {res}")
        time.sleep(1)  # 避免并发入库抖动

    print("\n汇总:")
    for name, res in results.items():
        if res.get("status") in ("ok", "exists"):
            print(f"  ✓ {name} -> doc_id={res.get('doc_id')}")
        else:
            print(f"  ✗ {name} -> {res}")
    print(f"\n测试用户: {u}")
    print("清理入口: python cleanup_eval_docs.py（按 [EVAL] 文件名+测试用户清理）")


if __name__ == "__main__":
    main()
