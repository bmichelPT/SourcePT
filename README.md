# Portal Escolar · SourcePT

Portal financeiro em português de Portugal. Os encarregados consultam os alunos associados à sua conta, o saldo e o histórico de cobranças e pagamentos. Os dados são consultados ao abrir a página, atualizados a cada 15 segundos enquanto o separador está visível e podem ser atualizados de imediato através de **Atualizar dados**. A secretaria regista os movimentos no próprio portal.

## Executar localmente

Instale Python, crie um ambiente virtual e execute `pip install -r requirements.txt`. Defina as variáveis de ambiente `DATABASE_URL` e `SECRET_KEY`. Para testes locais pode usar `DATABASE_URL=sqlite:///portal.sqlite` e `LOCAL_DEV=1`. Gere a chave com `python -c "import secrets; print(secrets.token_hex(32))"`.

```sh
flask --app app init-db
flask --app app create-account secretaria@example.org --admin
flask --app app create-account encarregado@example.org
flask --app app add-student A001 "Aluno de Exemplo" encarregado@example.org
flask --app app run
```

As palavras-passe são pedidas por interação. Use o endereço apresentado pelo Flask. Entre como secretaria, registe uma cobrança e um pagamento de exemplo e confirme o saldo entrando como encarregado.

## Publicar no Render

Envie os ficheiros para o repositório GitHub. No painel do Render, crie um Blueprint apontado ao repositório e reveja os planos pagos definidos em `render.yaml` antes de confirmar. O Blueprint cria o serviço web, gera a chave de sessão, liga o PostgreSQL e cria as tabelas antes do arranque. No Shell do serviço execute os comandos `create-account` e `add-student` com os dados reais. Mantenha `LOCAL_DEV` desativado no Render.

Antes de abrir o portal a famílias reais, configure limitação de tentativas de login na infraestrutura, defina um processo de recuperação de contas e confirme as rotinas de cópia de segurança. O portal regista pagamentos comunicados pela escola. A integração com sistemas de faturação ou pagamento depende de identificar o sistema utilizado.

