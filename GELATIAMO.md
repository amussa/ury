# Fork Gelatiamo do URY

Este repositório é um fork do [`ury-erp/ury`](https://github.com/ury-erp/ury) com as
customizações do Gelatiamo. Não é um clone descartável: **o que está na branch `gelatiamo`
é código nosso e não existe em mais lado nenhum.**

Este documento explica a topologia, como se puxam updates do upstream, e o que ainda não
está fechado. Só existe neste fork — o upstream nunca lhe toca, por isso nunca dá conflito.

---

## Topologia

| Remoto | URL | Papel |
|---|---|---|
| `upstream` | `https://github.com/ury-erp/ury` | Só leitura. Fonte dos updates. |
| `origin` | `git@github.com:amussa/ury.git` | Este fork. Onde se publica. |

| Branch | Segue | Papel |
|---|---|---|
| `develop` | `upstream/develop` | Espelho intocado do upstream. **Nunca commitar aqui.** |
| `gelatiamo` | `origin/gelatiamo` | Todas as customizações, sempre por cima do upstream. |

O `upstream` é também o *promisor* do partial clone (`--filter=blob:none`): é de lá que o git
vai buscar, a pedido, os blobs que não estão em disco. Não remover nem reconfigurar esse
remoto sem perceber isso — o repositório deixa de conseguir materializar ficheiros antigos.

---

## O que é customização nossa

Cada commit da `gelatiamo` corresponde a uma entrada de `patches/` no projecto Gelatiamo:

| Alteração | O que faz |
|---|---|
| `feat(branding)` | Substitui o nome e as imagens URY por "POS Vendas \| Gelatiamo", nos metadados do app Frappe e no shell React. |
| `feat(i18n)` | Traduções `pt` (`ury/translations/pt.csv`). |
| `build(pos)` | `base: '/assets/ury/pos/'` no Vite — o bundle é servido pelo Frappe a partir do directório público do app, não da raiz do domínio. |
| `feat(print)` | Print Formats com `raw_printing` vão em ESC/POS directo para a impressora térmica, em vez de serem rasterizados como HTML pelo QZ. |
| `fix(pos)` roles | Papéis lidos via `frappe.core.doctype.user.user.get_roles` (o User doc só devolve o que a sessão pode ler), e cache do perfil POS por utilizador — numa caixa partilhada o operador seguinte herdava a filial do anterior. |
| `feat(pos)` caixa | `ury/ury_pos/cashier.py`: o caixa vem da POS Opening Entry aberta, não do primeiro nome de `applicable_for_users`. |

Os SHAs mudam a cada rebase — por isso esta tabela lista assuntos e não commits. Para ver o
que é nosso num dado momento:

```bash
git log --oneline upstream/develop..gelatiamo
git diff upstream/develop..gelatiamo --stat
```

---

## Puxar updates do upstream

```bash
git fetch upstream
git switch develop && git merge --ff-only upstream/develop
git switch gelatiamo && git rebase upstream/develop
# resolver conflitos, se os houver
git push --force-with-lease origin gelatiamo
```

Rebase e não merge: mantém os nossos patches como um bloco limpo por cima do upstream, o que
faz com que `upstream/develop..gelatiamo` seja sempre a resposta exacta à pergunta "o que é
que nós mudámos".

`--force-with-lease` e não `--force`: é o que impede um push de esmagar trabalho que não
tenhas visto.

**Onde os conflitos aparecem:** `ury/hooks.py` e `pos/index.html` são substituições de
branding sobre linhas que o upstream também mexe. Nos dois casos, a resolução é normalmente
manter o nosso lado.

**Quando o upstream corrige o mesmo que nós:** acontece — em agosto de 2026 o upstream landou
`fix: add roundedTotal prop to PaymentDialog component`, a mesma correcção que já tínhamos
localmente. Nesses casos larga-se o nosso commit durante o rebase (`git rebase --skip`) e
fica-se com a versão upstream, que é a que vai ser mantida.

---

## Avisos

### `sync-source.sh pull` larga esta branch

O `scripts/sync-source.sh` do projecto Gelatiamo faz `git checkout --detach <sha>` a partir do
`versions.lock`. Corrê-lo aqui move a árvore de trabalho para o código upstream e larga a
`gelatiamo`.

**Nada se perde** — a ref da branch sobrevive. Recupera-se com:

```bash
git switch gelatiamo
```

### Estes commits ainda não chegam a produção

O `repo_url()` do `sync-source.sh` tem o URL do upstream fixo, por isso o `apps.json` do build
manda clonar `ury-erp/ury` e não este fork. Na prática, **a imagem Docker não leva nada do que
está aqui**: quem manda em produção continua a ser `deploy/hotfixes/`, com bundles JS
pré-compilados.

Fechar esta lacuna implica apontar `repo_url()` e o `versions.lock` ao fork, reescrever a
doutrina do `source/README.md` (que declara `source/` como "só para consulta") e aposentar os
hotfixes. Ficou por decidir — até lá, **uma alteração commitada aqui não está em produção**.

---

## Contribuir de volta ao upstream

Parte do que está aqui é específico do Gelatiamo (branding, traduções `pt`) e nunca sobe. Mas
as correcções genéricas — impressão térmica, resolução de papéis, dono da caixa pela abertura —
são candidatas a PR. Estarem em commits temáticos separados é precisamente o que torna isso
possível: `git cherry-pick` do commit para uma branch limpa a partir de `upstream/develop`.
