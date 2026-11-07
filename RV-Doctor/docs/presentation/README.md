# 演示材料

- `RV-Doctor成果提交.pptx`：可编辑演示文稿。
- `RV-Doctor成果提交.pdf`：导出预览。
- `build_ppt.js`：原始生成脚本，数据口径来自 `reports/data/`。

仅查看材料无需安装 Node.js 依赖。如需重新生成，在本目录安装 `pptxgenjs` 后运行：

```powershell
npm install --no-save --package-lock=false pptxgenjs
node build_ppt.js
```

PPTX 输出到本目录；PDF 需要另行导出。本次整理只迁移现有材料、修正生成脚本路径，没有重建或更新文稿数据。
