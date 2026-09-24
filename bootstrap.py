"""
bootstrap.py
Ponto de entrada padrao chamado pelo Automation Anywhere (ou execucao manual).
NAO muda entre robos - o que muda e a pasta dentro de bots/<nome_bot>/.
 
Fluxo:
1. Recebe os parametros do AA -> $vParams$ = "AMBIENTE,NOME_BOT,STREXECUTARBOT"
2. Cria (ou reaproveita) um venv isolado para aquele bot: bots/<nome_bot>/.venv
3. Instala bots/<nome_bot>/requirements.txt dentro desse venv
4. Executa bots/<nome_bot>/main.py usando o python do venv, passando o ambiente como argumento
 
Isolar em venv por bot evita conflito de versao de lib entre robos diferentes
rodando na mesma VM (ex: bot A precisa de selenium 4.15, bot B de selenium 4.21).
"""
 
import json
from logging.handlers import TimedRotatingFileHandler
import os
import subprocess
import venv
import logging
from datetime import datetime
import shutil
from urllib.parse import urlparse, parse_qs, unquote

BASE_DIR = r"C:\RPA\repo"
BOTS_DIR = os.path.join(BASE_DIR, "bots")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# --------------- Tratativa de logs ---------------  #
os.makedirs(LOG_DIR, exist_ok=True)
# Configura o logger
log = logging.getLogger(__name__)

# Define o nível de log para INFO
log.setLevel(logging.INFO)

# Configura o arquivo de log com rotação diária
current_log = os.path.join(LOG_DIR, datetime.now().strftime("%d%m%Y.log"))

# Configura o manipulador de log para rotação diária
file_handler = TimedRotatingFileHandler(
    current_log, when="midnight", interval=1, backupCount=30, encoding="utf-8"
)
# Renomeia os arquivos de log para o formato "ddMMyyyy.log" em vez do padrão "nome_arquivo.log.YYYY-MM-DD"
file_handler.namer = lambda name: os.path.join(
    LOG_DIR, datetime.strptime(name.rsplit(".log.", 1)[1], "%Y-%m-%d").strftime("%d%m%Y.log")
)

# Configura o formato do log básico para incluir timestamp, nível de log e mensagem
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[file_handler, logging.StreamHandler()],
)

log = logging.getLogger(__name__)
# --------------- Tratativa de logs ---------------  #
def parse_caminho_repositorio(caminho_repositorio: str):
    """
    Aceita dois formatos de URL do Azure DevOps:
    1) .../_git/Repo?path=/automacoes/eco_bot   (formato com query string)
    2) .../_git/Repo/automacoes/eco_bot          (formato com path direto)
    Retorna (url_clone, subpasta).
    """
    parsed = urlparse(caminho_repositorio)

    query = parse_qs(parsed.query)
    path_param = query.get("path", [None])[0]

    if path_param:
        # Formato 1: subpasta vem da query string
        url_clone = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        subpasta = unquote(path_param).lstrip("/")
    else:
        # Formato 2: subpasta vem embutida no path, depois de "_git/<repo>"
        partes = parsed.path.split("/_git/")
        if len(partes) == 2:
            base = partes[0]  # ex: /hypera/Repositorio_Automacoes_Python
            resto = partes[1].split("/", 1)  # separa nome do repo do restante
            nome_repo = resto[0]
            subpasta = resto[1] if len(resto) > 1 else None
            url_clone = f"{parsed.scheme}://{parsed.netloc}{base}/_git/{nome_repo}"
        else:
            # Nao encontrou "_git/", assume que a URL inteira eh o clone, sem subpasta
            url_clone = caminho_repositorio
            subpasta = None

    return url_clone, subpasta

def repo_existe(pasta_destino: str) -> bool:
    """Verifica se ja existe um clone Git valido na pasta do bot."""
    return os.path.isdir(os.path.join(pasta_destino, ".git"))


def atualizar_bot(pasta_destino: str) -> None:
    """Faz pull no bot que ja foi clonado anteriormente (respeita o sparse-checkout ja configurado)."""
    log.info(f"[bootstrap] Repositorio ja existe em {pasta_destino}, atualizando com git pull.")
    resultado = subprocess.run(
        ["git", "pull"], cwd=pasta_destino, capture_output=True, text=True
    )
    if resultado.returncode != 0:
        raise RuntimeError(
            f"Falha ao atualizar (git pull) em '{pasta_destino}': {resultado.stderr.strip()}"
        )
    log.info(f"[bootstrap] Repositorio atualizado em {pasta_destino}.")


def clonar_bot(caminho_repositorio: str, pasta_destino: str):
    """Clona o bot pela primeira vez. So deve ser chamada quando a pasta ainda nao tem um clone valido."""
    url_clone, subpasta = parse_caminho_repositorio(caminho_repositorio)
    log.info(f"[bootstrap] URL clone resolvida: {url_clone}")
    log.info(f"[bootstrap] Subpasta resolvida: {subpasta}")

    if os.path.exists(pasta_destino):
        log.info(f"[bootstrap] Pasta destino ja existe (sem clone valido), removendo: {pasta_destino}")
        shutil.rmtree(pasta_destino)

    os.makedirs(pasta_destino, exist_ok=True)

    if subpasta:
        log.info(f"[bootstrap] Clonando com sparse checkout | Repo={url_clone} | Subpasta={subpasta}")

        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", url_clone, pasta_destino],
            check=True
        )
        subprocess.run(["git", "sparse-checkout", "init", "--cone"], cwd=pasta_destino, check=True)
        subprocess.run(["git", "sparse-checkout", "set", subpasta], cwd=pasta_destino, check=True)
        subprocess.run(["git", "checkout"], cwd=pasta_destino, check=True)

        pasta_subpasta = os.path.join(pasta_destino, *subpasta.split("/"))

        if not os.path.isdir(pasta_subpasta):
            shutil.rmtree(pasta_destino, ignore_errors=True)
            raise FileNotFoundError(
                f"A pasta '{subpasta}' nao foi encontrada no repositorio {url_clone}. "
                f"Verifique se o caminho esta correto."
            )

        # Nomes dos itens que realmente pertencem a subpasta pedida
        itens_validos = set(os.listdir(pasta_subpasta))

        log.info(f"[bootstrap] Pasta '{subpasta}' encontrada, promovendo conteudo para {pasta_destino}")
        for item in itens_validos:
            origem = os.path.join(pasta_subpasta, item)
            destino = os.path.join(pasta_destino, item)
            if os.path.exists(destino):
                shutil.rmtree(destino) if os.path.isdir(destino) else os.remove(destino)
            shutil.move(origem, destino)

        # Remove a arvore de pastas intermediarias (ex: automacoes/eco_bot)
        raiz_intermediaria = os.path.join(pasta_destino, subpasta.split("/")[0])
        shutil.rmtree(raiz_intermediaria, ignore_errors=True)

        # Remove qualquer arquivo/pasta extra que veio da raiz do repo (cone mode),
        # mantendo somente .git e o que veio da subpasta
        log.info("[bootstrap] Limpando arquivos extras trazidos da raiz do repositorio")
        for item in os.listdir(pasta_destino):
            if item == ".git" or item in itens_validos:
                continue
            caminho_item = os.path.join(pasta_destino, item)
            shutil.rmtree(caminho_item) if os.path.isdir(caminho_item) else os.remove(caminho_item)
    else:
        log.info(f"[bootstrap] Clonando repositorio completo | Repo={url_clone}")
        subprocess.run(["git", "clone", url_clone, pasta_destino], check=True)


def garantir_bot(caminho_repositorio: str, pasta_destino: str) -> None:
    """Ponto de entrada unico: decide entre atualizar (pull) ou clonar, dependendo se ja existe um clone valido."""
    if repo_existe(pasta_destino):
        atualizar_bot(pasta_destino)
    else:
        clonar_bot(caminho_repositorio, pasta_destino)


def diretorio_bot(nome_bot: str) -> str:
    pasta = os.path.join(BOTS_DIR, nome_bot)
    if not os.path.isdir(pasta):
        os.makedirs(pasta, exist_ok=True)
    return pasta
 
 
def diretorio_venv(nome_bot: str) -> str:
    return os.path.join(diretorio_bot(nome_bot), ".env")
 
 
def diretorio_python_venv(nome_bot: str) -> str:
    venv_dir = diretorio_venv(nome_bot)
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")
 
 
def garantir_venv(nome_bot: str) -> None:
    """Equivalente a 'python -m venv', mas via API (venv.EnvBuilder) - cria so se nao existir."""
    venv_dir = diretorio_venv(nome_bot)
    python_venv = diretorio_python_venv(nome_bot)
 
    if os.path.exists(python_venv):
        log.info(f"[bootstrap] env de '{nome_bot}' ja existe, reutilizando.")
        
        return
 
    log.info(f"[bootstrap] Criando env para '{nome_bot}' em {venv_dir}...")
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    log.info(f"[bootstrap] env criado.")
 
 
def instalar_dependencias(nome_bot: str) -> None:
   

    python_venv = diretorio_python_venv(nome_bot)
    requirements_path = os.path.join(diretorio_bot(nome_bot), "requirements.txt")

    if not os.path.exists(requirements_path):
        log.info(f"[bootstrap] requirements.txt nao encontrado para '{nome_bot}', pulando instalacao.")
        return

    garantir_venv(nome_bot=nome_bot)

    log.info(f"[bootstrap] Instalando dependencias de '{nome_bot}' no env...")
    subprocess.check_call([
        python_venv, "-m", "pip", "install", "-r", requirements_path,
        "--quiet", "--disable-pip-version-check",
    ])
    log.info(f"[bootstrap] Dependencias de '{nome_bot}' instaladas com sucesso!")


def rodar_bot(nome_bot: str, ambiente: str, params: str) -> str:
    python_venv = diretorio_python_venv(nome_bot)
    main_path = os.path.join(diretorio_bot(nome_bot), "main.py")
 
    if not os.path.exists(main_path):
        raise FileNotFoundError(f"main.py nao encontrado em bots/{nome_bot}/")
 
    log.info(f"[bootstrap] Executando '{nome_bot}' (ambiente={ambiente}) via env proprio...")
    resultado = subprocess.run(
        [python_venv, main_path, ambiente, params],
        cwd=diretorio_bot(nome_bot),
        capture_output=True,
        text=True,
    )

    resposta = resultado.stdout.strip()
    erro_tecnico = resultado.stderr.strip()

    if resultado.returncode != 0:
        detalhe = f": {erro_tecnico}" if erro_tecnico else ""
        raise RuntimeError(
            f"Bot '{nome_bot}' terminou com erro "
            f"(codigo {resultado.returncode}){detalhe}"
        )

    if not resposta:
        raise RuntimeError(
            f"Bot '{nome_bot}' terminou sem produzir uma resposta no stdout."
        )

    if erro_tecnico:
        log.warning(
            f"[bootstrap] '{nome_bot}' terminou com mensagens no stderr."
        )

    log.info(f"[bootstrap] '{nome_bot}' finalizado com sucesso.")
    return resposta
 
 
def executar(params: str) -> str:
    """
    Chamada pelo AA: 'Python script: Execute function "executar" with parameter $vParams$'
    params no formato: "AMBIENTE,NOME_BOT,CAMINHO_GIT"  ex: "PROD,R01_Teste,/caminho/para/o/repo"
    """
    ambiente = None
    nome_bot = None
    resposta = None
    try:
        log.info(f"[bootstrap] Iniciando bootstrap com parametros: {params}")
        dados = json.loads(params)
        ambiente = dados.get("ambiente", "DEV").strip().upper()
        nome_bot = dados.get("nomebot")
        rodarBot = str(dados.get("executar_bot", "True")).strip().lower() == "true"
        caminho_git = (dados.get("caminho_repositorio") or "").strip()
        parametros = json.dumps(dados.get("parametros", {}), ensure_ascii=False)

        if ambiente not in ["DEV", "HML", "PROD"]:
            raise ValueError(
                f"Ambiente informado ({ambiente}) nao eh valido. Valores permitidos: DEV, HML, PROD"
            )

        if not nome_bot:
            raise ValueError('Campo "nomebot" nao informado na entrada JSON.')

        log.info(f"[bootstrap] Ambiente={ambiente} | Bot={nome_bot} | RodarBot={rodarBot} | Caminho Git={caminho_git}")

        if caminho_git:
            garantir_bot(caminho_git, diretorio_bot(nome_bot))

        instalar_dependencias(nome_bot)

        if rodarBot:
            resposta_texto = rodar_bot(nome_bot, ambiente, parametros)
            resposta_texto = resposta_texto.splitlines()
            try:
                resposta = json.loads(resposta_texto[-1])
            except json.JSONDecodeError as erro:
                raise RuntimeError(
                    f"Bot '{nome_bot}' retornou um JSON invalido no stdout."
                ) from erro
            log.info(f"[bootstrap] Rodou com sucesso Bot={nome_bot} no Ambiente={ambiente}")

        payload = {
            "status": "sucesso",
            "bot": nome_bot,
            "ambiente": ambiente,
            "mensagem": "Bootstrap concluido com sucesso.",
            "resposta": resposta
        }

    except Exception as e:
        log.error(f"[bootstrap] Erro ao executar bootstrap: {e}", exc_info=True)
        payload = {
            "status": "falha",
            "bot": nome_bot,
            "ambiente": ambiente,
            "mensagem": str(e),
        }
    log.info(f"[bootstrap] Payload de retorno: {payload}")
    return json.dumps(payload, ensure_ascii=False)

    
 
if __name__ == "__main__":
    # Teste local: python bootstrap.py Ambiente, Nome do robo Ex: "DEV,R01_HYPERA"
    #parametro = '{"ambiente": "DEV", "nomebot": "R01", "executar_bot": "False", "caminho_repositorio": "https://dev.azure.com/hypera/Repositorio_Automacoes_Python/_git/DEV"}'
    parametro = '{"ambiente": "DEV", "nomebot": "EcoBot", "executar_bot": "True", "caminho_repositorio": "https://dev.azure.com/hypera/Repositorio_Automacoes_Python/_git/Repositorio_Automacoes_Python/automacoes/eco_bot"}'
    # parametro = '{"ambiente": "DEV", "nomebot": "AutomacoesHypera", "executar_bot": "False", "caminho_repositorio": "https://dev.azure.com/hypera/Repositorio_Automacoes_Python/_git/Repositorio_Automacoes_Python/automacoes/"}'
    resposta = executar(parametro)
    print(f"Bootstrap finalizado com status: {resposta}")