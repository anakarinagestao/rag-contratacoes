# Avaliação factual offline

`factual_evidence_annotations_pending.json` é um conjunto de anotações propostas. Os candidatos e textos foram extraídos de uma cópia temporária somente leitura de `app.db`; nenhum texto é considerado verdade de referência antes da revisão humana.

Cada candidato deve receber, por aspecto, uma das classificações:

- `direct`: o trecho responde diretamente ao aspecto;
- `complementary`: o trecho é necessário para completar uma evidência distribuída;
- `thematic_only`: apenas menciona o tema ou contém coincidência lexical;
- `irrelevant`: não sustenta o aspecto.

O revisor deve preencher `aspect`, `label` e mudar `review_status` para `approved` somente quando puder justificar a classificação pelo texto e pelo documento de origem. Perguntas sem evidência suficiente devem permanecer sem cobertura completa; isso é um resultado válido.

As comparações usam os rankings produzidos pelo protótipo multi-query e pelo trace original. `Recall@k` considera apenas candidatos aprovados como `direct` ou `complementary`; `Precision@k` considera a proporção desses candidatos no corte; cobertura mede aspectos distintos sustentados; redundância mede hits que não acrescentam aspecto; contaminação documental mede hits aprovados vindos de documentos adicionais. Registros `pending` são excluídos das métricas.

O teste `test_factual_evidence_evaluation.py` valida o esquema, a geração dos candidatos e a regra de que métricas não usam anotações pendentes. Para regenerar o arquivo após atualizar o banco, em ambiente offline:

```powershell
$env:FACTUAL_ANNOTATIONS_OUTPUT='tests/fixtures/factual_evidence_annotations_pending.json'
.venv/Scripts/python.exe -B -m pytest tests/integration/test_factual_evidence_evaluation.py -q -p no:cacheprovider
```

Não há classificação automática definitiva, nem chamada a LLM, embeddings externos ou rede.
