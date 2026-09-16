import json
import os
import flet as ft
import pandas as pd
import subprocess
import requests
import base64
import shutil
import http.server
import socketserver
import threading
import webbrowser
import time
import re
import urllib.request
import sys
import socket
import tempfile
import zipfile
import io
import paramiko
import select
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PyTunnel")


# ============================================================
# ===== CLASSE PyTunnel (TÚNEL REVERSO SSH) =================
# ============================================================

class PyTunnel:
    """
    Túnel reverso SSH com fallback entre múltiplos provedores.
    Ordem: Pinggy → localhost.run → srv.us
    """

    PROVEDORES = [
        {
            "nome": "Pinggy",
            "server": "free.pinggy.io",
            "port": 443,
            "user": "free",
            "auth": "publickey",
            "tipo_chave": "ed25519",
            "porta_remota": 0,
            "regex_url": r'https://[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-\.]*(?:run\.pinggy-free\.link|free\.pinggy\.net|a\.pinggy\.link|pinggy\.online)',
            "requer_shell": True,
            "limite_min": 60,
        },
        {
            "nome": "localhost.run",
            "server": "localhost.run",
            "port": 22,
            "user": "nokey",
            "auth": "none",
            "tipo_chave": None,
            "porta_remota": 0,
            "regex_url": r'https://[a-zA-Z0-9\-]+\.lhr\.life',
            "requer_shell": False,
            "limite_min": 0,
        },
        {
            "nome": "srv.us",
            "server": "srv.us",
            "port": 22,
            "user": "",
            "auth": "publickey",
            "tipo_chave": "ed25519",
            "porta_remota": 1,
            "regex_url": r'https://[a-zA-Z0-9\-]+\.srv\.us',
            "requer_shell": False,
            "limite_min": 0,
        },
    ]

    def __init__(self, local_host="127.0.0.1", local_port=8550,
                 key_path=None, provedor_preferido=None):
        self.local_host = local_host
        self.local_port = local_port
        # ✅ CORRIGIDO: chave SSH fica na pasta persistente do app
        if key_path is None:
            pasta_persistente = os.getenv("FLET_APP_STORAGE_DATA") \
                or os.path.dirname(os.path.abspath(__file__))
            key_path = os.path.join(pasta_persistente, "tunnel_key")
        self.key_path = key_path
        self.provedor_preferido = provedor_preferido

        self.ssh_client = None
        self.transport = None
        self.is_running = False
        self.public_url = None
        self._thread = None
        self._channels = []
        self.log_callback = None
        self.status_callback = None

        self.provedor_atual = None
        self.inicio_conexao = None
        self.tentativas_reconexao = 0
        self.max_tentativas = 5

    def set_log_callback(self, callback):
        self.log_callback = callback

    def set_status_callback(self, callback):
        self.status_callback = callback

    def _log(self, mensagem):
        logger.info(mensagem)
        if self.log_callback:
            try:
                self.log_callback(mensagem)
            except:
                pass

    def _notificar_status(self, status, mensagem, detalhes=""):
        if self.status_callback:
            try:
                self.status_callback(status, mensagem, detalhes)
            except:
                pass

    def _load_or_generate_key(self, provedor):
        tipo_chave = provedor.get("tipo_chave")
        if not tipo_chave:
            return None

        caminho_chave = f"{self.key_path}_{tipo_chave}"

        if os.path.exists(caminho_chave):
            self._log(f"🔑 Carregando chave {tipo_chave.upper()}...")
            try:
                with open(caminho_chave, "r") as f:
                    conteudo = f.read()
                if tipo_chave == "rsa":
                    return paramiko.RSAKey.from_private_key(io.StringIO(conteudo))
                else:
                    return paramiko.Ed25519Key.from_private_key(io.StringIO(conteudo))
            except Exception as e:
                self._log(f"⚠️ Chave corrompida, gerando nova")
                try:
                    os.remove(caminho_chave)
                except:
                    pass

        self._log(f"🔑 Gerando nova chave {tipo_chave.upper()}...")

        if tipo_chave == "rsa":
            key = paramiko.RSAKey.generate(2048)
            try:
                key.write_private_key_file(caminho_chave)
                self._log(f"✅ Chave RSA salva")
            except Exception as e:
                self._log(f"⚠️ Não salvou: {e}")
            return key
        else:
            try:
                from cryptography.hazmat.primitives.asymmetric import ed25519
                from cryptography.hazmat.primitives import serialization

                private_key = ed25519.Ed25519PrivateKey.generate()
                private_bytes = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.OpenSSH,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                chave_str = private_bytes.decode("utf-8")

                pasta = os.path.dirname(caminho_chave)
                if pasta and not os.path.exists(pasta):
                    os.makedirs(pasta, exist_ok=True)
                with open(caminho_chave, "w") as f:
                    f.write(chave_str)
                self._log(f"✅ Chave Ed25519 salva")

                return paramiko.Ed25519Key.from_private_key(io.StringIO(chave_str))
            except Exception as e:
                self._log(f"❌ Erro Ed25519: {e}")
                key = paramiko.RSAKey.generate(2048)
                return key

    def _handler_conexao(self, chan):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect((self.local_host, self.local_port))
        except Exception as e:
            self._log(f"❌ Local falhou ({self.local_host}:{self.local_port}): {e}")
            try:
                chan.close()
            except:
                pass
            return
        try:
            while self.is_running:
                r, _, _ = select.select([sock, chan], [], [], 1.0)
                if not self.is_running:
                    break
                if chan in r:
                    data = chan.recv(4096)
                    if not data:
                        break
                    sock.sendall(data)
                if sock in r:
                    data = sock.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
        except Exception:
            pass
        finally:
            try:
                chan.close()
            except:
                pass
            try:
                sock.close()
            except:
                pass

    def _loop_aceitar(self):
        self._log("🔄 Loop de escuta iniciado")
        while self.is_running and self.transport and self.transport.is_active():
            try:
                chan = self.transport.accept(1)
                if chan is None:
                    continue
                self._log(f"🌐 Requisição externa recebida!")
                self._channels.append(chan)
                threading.Thread(
                    target=self._handler_conexao, args=(chan,), daemon=True
                ).start()
            except Exception:
                if self.is_running:
                    pass
                break
        self._log("🛑 Loop de escuta encerrado")

    def _capturar_url_shell(self, provedor):
        try:
            shell = self.ssh_client.invoke_shell()
            shell.settimeout(1.0)
            dados = b""
            inicio = time.time()
            while time.time() - inicio < 12:
                try:
                    if shell.recv_ready():
                        chunk = shell.recv(4096)
                        if chunk:
                            dados += chunk
                            texto = dados.decode('utf-8', errors='ignore')
                            texto_limpo = re.sub(
                                r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', texto
                            )
                            matches = re.findall(provedor["regex_url"], texto_limpo)
                            for m in matches:
                                if "dashboard" not in m and len(m) > 15:
                                    try:
                                        shell.close()
                                    except:
                                        pass
                                    return m.strip()
                except socket.timeout:
                    pass
                time.sleep(0.3)
            try:
                shell.close()
            except:
                pass
            return None
        except Exception as e:
            self._log(f"⚠️ Erro ao capturar URL via shell: {e}")
            return None

    def _capturar_url_channel(self, provedor):
        try:
            chan = self.transport.open_session()
            chan.settimeout(2)
            buffer = ""
            inicio = time.time()
            while time.time() - inicio < 15:
                try:
                    if chan.recv_ready():
                        data = chan.recv(4096).decode('utf-8', errors='ignore')
                        if data:
                            buffer += data
                            for linha in data.splitlines():
                                if linha.strip():
                                    self._log(f"   📥 {linha.strip()[:120]}")
                            matches = re.findall(provedor["regex_url"], buffer)
                            for m in matches:
                                try:
                                    chan.close()
                                except:
                                    pass
                                return m.strip()
                except socket.timeout:
                    pass
                except Exception:
                    break
                time.sleep(0.2)
            try:
                chan.close()
            except:
                pass
            return None
        except Exception as e:
            self._log(f"⚠️ Erro ao capturar URL via channel: {e}")
            return None

    def _conectar_ssh(self, provedor, key):
        common = {
            "hostname": provedor["server"],
            "port": provedor["port"],
            "username": provedor["user"],
            "look_for_keys": False,
            "allow_agent": False,
            "timeout": 45,
            "banner_timeout": 45,
            "auth_timeout": 45,
        }

        if provedor["auth"] == "none":
            self._log("🔓 Usando autenticação NONE...")
            try:
                from paramiko.auth_strategy import NoneAuth
                self.ssh_client.connect(auth_strategy=NoneAuth(""), **common)
            except ImportError:
                try:
                    self.ssh_client.connect(**common)
                except paramiko.SSHException:
                    self.ssh_client.get_transport().auth_none(provedor["user"])
        else:
            self.ssh_client.connect(pkey=key, **common)

    def _tentar_provedor(self, provedor, indice):
        self._log(f"")
        self._log(f"━━━ Tentando {provedor['nome']} ({indice+1}/{len(self.PROVEDORES)}) ━━━")
        self._notificar_status("conectando", f"Conectando em {provedor['nome']}...")

        try:
            key = self._load_or_generate_key(provedor)

            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self._log(f"🌐 {provedor['server']}:{provedor['port']} (user: {provedor['user'] or 'vazio'})...")
            self._conectar_ssh(provedor, key)
            self._log(f"✅ SSH conectado!")

            self.transport = self.ssh_client.get_transport()
            self.transport.set_keepalive(30)

            porta_remota = provedor.get("porta_remota", 0)
            self._log(f"🔌 Solicitando porta remota {porta_remota}...")
            self.transport.request_port_forward('', porta_remota)

            self.is_running = True
            self._thread = threading.Thread(target=self._loop_aceitar, daemon=True)
            self._thread.start()

            if provedor["requer_shell"]:
                url = self._capturar_url_shell(provedor)
            else:
                url = self._capturar_url_channel(provedor)

            if not url:
                self._log(f"⚠️ {provedor['nome']} não retornou URL")
                self._fechar_conexao()
                return None

            self.public_url = url
            self.provedor_atual = provedor
            self.inicio_conexao = time.time()
            self.tentativas_reconexao = 0

            self._log(f"🎉 Túnel ativo em {provedor['nome']}: {url}")
            self._notificar_status(
                "ativo",
                f"✅ Túnel ativo via {provedor['nome']}",
                url
            )
            return url

        except Exception as e:
            self._log(f"❌ {provedor['nome']} falhou: {type(e).__name__}: {e}")
            self._fechar_conexao()
            return None

    def _fechar_conexao(self):
        self.is_running = False
        for chan in self._channels:
            try:
                chan.close()
            except:
                pass
        self._channels.clear()
        if self.transport:
            try:
                self.transport.close()
            except:
                pass
            self.transport = None
        if self.ssh_client:
            try:
                self.ssh_client.close()
            except:
                pass
            self.ssh_client = None

    def start(self):
        if self.public_url and self.is_running:
            return self.public_url

        self.tentativas_reconexao = 0

        if self.provedor_preferido is not None:
            provedor = self.PROVEDORES[self.provedor_preferido]
            self._log(f"🎯 Modo manual: testando apenas {provedor['nome']}")
            url = self._tentar_provedor(provedor, self.provedor_preferido)
            if url:
                return url
            return None

        for i, provedor in enumerate(self.PROVEDORES):
            url = self._tentar_provedor(provedor, i)
            if url:
                return url
            time.sleep(2)

        self._log("❌ Todos os provedores falharam")
        self._notificar_status("erro", "❌ Nenhum provedor disponível")
        return None

    def stop(self):
        self._log("🛑 Parando túnel...")
        self._notificar_status("parado", "Túnel parado")
        self.public_url = None
        self._fechar_conexao()
        self._log("✅ Parado")

    def is_active(self):
        return (self.is_running
                and self.transport is not None
                and self.transport.is_active())

    def tempo_restante(self):
        if not self.inicio_conexao or not self.provedor_atual:
            return None
        limite = self.provedor_atual.get("limite_min", 0)
        if limite == 0:
            return None
        decorrido = time.time() - self.inicio_conexao
        restante = (limite * 60) - decorrido
        return max(0, int(restante))


# ============================================================
# ===== CONFIGURAÇÕES DE ANÚNCIOS ============================
# ============================================================

LINK_DIRETO = "https://www.profitableratecpmnetwork.com/ih67c0tk?key=0fbe6afc2bc12224f11e10c034716ffb"

# ============================================================
# ===== CONFIGURAÇÕES DE PASTAS (PERSISTENTE NO ANDROID) =====
# ============================================================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))


def obter_pasta_dados():
    """
    Retorna uma pasta PERSISTENTE para salvar os dados do SimplyON.

    - No Android (APK): usa FLET_APP_STORAGE_DATA — pasta de dados do app.
      Só é apagada se o usuário DESINSTALAR. Sobrevive a:
        * botão "quadrado" (recentes)
        * app morto pelo sistema
        * reinicialização do celular

    - No PC (VSCode): FLET_APP_STORAGE_DATA geralmente não existe,
      então cai pra pasta do script (PASTA_ATUAL).
    """
    # 1ª tentativa: variável oficial do Flet
    pasta = os.getenv("FLET_APP_STORAGE_DATA")

    # 2ª tentativa: variável de compatibilidade
    if not pasta:
        pasta = os.getenv("FLET_APP_PATH")

    # 3ª tentativa: fallback PC/VSCode
    if not pasta:
        pasta = PASTA_ATUAL

    # Garante que existe e é gravável
    try:
        os.makedirs(pasta, exist_ok=True)
        teste = os.path.join(pasta, ".teste_escrita")
        with open(teste, "w") as f:
            f.write("ok")
        os.remove(teste)
        return pasta
    except Exception as e:
        print(f"⚠️ Falha ao usar {pasta}: {e}. Caindo pra PASTA_ATUAL.")
        return PASTA_ATUAL


PASTA_DADOS = obter_pasta_dados()
print(f"📁 Pasta de dados: {PASTA_DADOS}")

# Log de diagnóstico — grava num arquivo pra você ver o que rolou no Android
try:
    with open(os.path.join(PASTA_DADOS, "_debug_pasta.txt"), "a", encoding="utf-8") as _f:
        _f.write(f"[BOOT] PASTA_ATUAL={PASTA_ATUAL}\n")
        _f.write(f"[BOOT] FLET_APP_STORAGE_DATA={os.getenv('FLET_APP_STORAGE_DATA')}\n")
        _f.write(f"[BOOT] FLET_APP_PATH={os.getenv('FLET_APP_PATH')}\n")
        _f.write(f"[BOOT] PASTA_DADOS={PASTA_DADOS}\n")
        _f.write(f"[BOOT] Android? {'ANDROID_ROOT' in os.environ}\n")
        _f.write("-" * 40 + "\n")
except Exception as _e:
    print(f"⚠️ Não foi possível gravar _debug_pasta.txt: {_e}")

ARQUIVO_JSON = os.path.join(PASTA_DADOS, "estoque.json")
ARQUIVO_CONFIG = os.path.join(PASTA_DADOS, "config.json")
ARQUIVO_HTML = os.path.join(PASTA_DADOS, "index.html")
ARQUIVO_UPLOAD_CONFIG = os.path.join(PASTA_DADOS, "upload_config.json")
PASTA_IMAGENS = os.path.join(PASTA_DADOS, "imagens")
PASTA_BIN = os.path.join(PASTA_DADOS, "bin")

os.makedirs(PASTA_IMAGENS, exist_ok=True)
os.makedirs(PASTA_BIN, exist_ok=True)


def carregar_json(arquivo, padrao):
    if not os.path.exists(arquivo):
        return padrao
    with open(arquivo, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return padrao


def salvar_json(arquivo, dados):
    with open(arquivo, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


def obter_endereco_servidor():
    if 'ANDROID_ROOT' in os.environ:
        return "127.0.0.1"
    else:
        return "localhost"


def obter_ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# ============================================================
# ===== SERVIDOR WEB LOCAL ===================================
# ============================================================

class HandlerComDiretorio(http.server.SimpleHTTPRequestHandler):
    """Handler que SEMPRE serve da PASTA_DADOS (onde o index.html é gerado)."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PASTA_DADOS, **kwargs)

    def log_message(self, format, *args):
        pass


def iniciar_servidor_web():
    porta = 8550
    try:
        with socketserver.ThreadingTCPServer(("0.0.0.0", porta), HandlerComDiretorio) as httpd:
            print(f"🌐 Servidor rodando na porta {porta}")
            print(f"📱 Acesse: http://{obter_endereco_servidor()}:{porta}")
            ip_local = obter_ip_local()
            print(f"📱 Na rede local: http://{ip_local}:{porta}")
            httpd.serve_forever()
    except Exception as e:
        print(f"❌ Erro no servidor web: {e}")


def disparar_servidor_em_segundo_plano():
    t = threading.Thread(target=iniciar_servidor_web, daemon=True)
    t.start()
    time.sleep(2)


# ============================================================
# ===== TÚNEL PYTUNNEL =======================================
# ============================================================
link_publico = ""
tunel_ativo = False
pytunnel_instance = None


def iniciar_tunel_pytunnel(porta=8550):
    """Inicia o túnel PyTunnel. Retorna mensagem com o link ou erro."""
    global link_publico, tunel_ativo, pytunnel_instance

    try:
        pytunnel_instance = PyTunnel(
            local_host="127.0.0.1",
            local_port=porta,
            # ✅ CORRIGIDO: chave SSH persistente
            key_path=os.path.join(PASTA_DADOS, "tunnel_key"),
        )

        def log_cb(msg):
            print(f"[PyTunnel] {msg}")

        pytunnel_instance.set_log_callback(log_cb)

        url = pytunnel_instance.start()

        if url:
            link_publico = url
            tunel_ativo = True
            return f"✅ Túnel ativo! Link: {url}"
        else:
            return "❌ Todos os provedores falharam"

    except Exception as e:
        return f"❌ Erro no PyTunnel: {e}"


def parar_tunel():
    global tunel_ativo, link_publico, pytunnel_instance
    try:
        if pytunnel_instance:
            pytunnel_instance.stop()
            pytunnel_instance = None
        tunel_ativo = False
        link_publico = ""
    except Exception as e:
        print(f"Erro ao encerrar túnel: {e}")


def obter_config_nicho(nicho_escolhido):
    configs = {
        "🏍️ Peças de Moto Usada": {
            "icone": "🏍️",
            "categorias_padrao": ["Motor", "Suspensão", "Freio", "Transmissão", "Elétrica", "Carroceria"],
            "cores_sugeridas": ["#ff5722", "#000000", "#ff6b35"],
            "banners": ["PEÇAS ORIGINAIS PARA SUA MOTO", "CONFIANÇA E QUALIDADE EM CADA PEÇA", "MELHOR PREÇO DO MERCADO"],
            "descricao_padrao": "Peça original com garantia de fábrica. Pronta entrega.",
            "exemplos": ["Motor C100", "Amortecedor Dianteiro", "Pastilha de Freio", "Corrente de Transmissão"]
        },
        "🐶 PetShop / Animais": {
            "icone": "🐶",
            "categorias_padrao": ["Rações", "Brinquedos", "Banho e Tosa", "Acessórios", "Medicamentos", "Higiene"],
            "cores_sugeridas": ["#4caf50", "#8bc34a", "#ff9800"],
            "banners": ["CUIDADO E CARINHO PARA SEU PET", "OS MELHORES PRODUTOS PARA ANIMAIS", "AMAMOS SEU ANIMAL DE ESTIMAÇÃO"],
            "descricao_padrao": "Produto de alta qualidade para seu animal. Seguro e confiável.",
            "exemplos": ["Ração Golden 10kg", "Brinquedo Interativo", "Coleira Antipulgas", "Shampoo Hipoalergênico"]
        },
        "🚲 Bicicletas / Bike": {
            "icone": "🚲",
            "categorias_padrao": ["Aros", "Pneus", "Câmbio", "Freios", "Guidão", "Acessórios"],
            "cores_sugeridas": ["#2196f3", "#009688", "#ff5722"],
            "banners": ["VELOCIDADE E PERFORMANCE", "SUA BIKE SEMPRE NO PONTO", "EQUIPAMENTOS PARA CICLISTAS"],
            "descricao_padrao": "Componente de alta performance para sua bicicleta.",
            "exemplos": ["Caloi 10", "Aro 29", "Freio a Disco Shimano", "Guidão Profissional"]
        },
        "📱 Eletrônicos / Celulares": {
            "icone": "📱",
            "categorias_padrao": ["Smartphones", "Tablets", "Acessórios", "Notebooks", "TVs", "Áudio"],
            "cores_sugeridas": ["#000000", "#2196f3", "#9c27b0"],
            "banners": ["TECNOLOGIA DE PONTA", "OS MELHORES ELETRÔNICOS", "INOVAÇÃO E QUALIDADE"],
            "descricao_padrao": "Produto eletrônico com garantia e procedência.",
            "exemplos": ["iPhone 15 Pro", "Notebook Dell", "Fone Bluetooth", "Smart TV 50\""]
        },
        "👗 Moda / Roupas": {
            "icone": "👗",
            "categorias_padrao": ["Feminino", "Masculino", "Infantil", "Calçados", "Acessórios", "Plus Size"],
            "cores_sugeridas": ["#e91e63", "#9c27b0", "#ff4081"],
            "banners": ["ESTILO E ELEGÂNCIA", "MODA PARA TODOS OS ESTILOS", "VESTINDO SEU MELHOR"],
            "descricao_padrao": "Peça de alta qualidade, tecido premium.",
            "exemplos": ["Vestido Floral", "Tênis Esportivo", "Jaqueta Jeans", "Bolsa de Couro"]
        },
        "🛋️ Móveis / Decoração": {
            "icone": "🛋️",
            "categorias_padrao": ["Salas", "Quartos", "Cozinhas", "Escritórios", "Decoração", "Jardinagem"],
            "cores_sugeridas": ["#795548", "#8d6e63", "#a1887f"],
            "banners": ["AMBIENTE SEU ESPAÇO", "DECORAÇÃO E CONFORTO", "MÓVEIS PARA TODOS OS AMBIENTES"],
            "descricao_padrao": "Móvel com design moderno e durabilidade.",
            "exemplos": ["Sofá 3 Lugares", "Mesa de Jantar", "Estante Planejada", "Cama Box"]
        },
        "🍔 Alimentação / Mercado": {
            "icone": "🍔",
            "categorias_padrao": ["Frios", "Bebidas", "Padaria", "Carnes", "Hortifruti", "Mercearia"],
            "cores_sugeridas": ["#ff5722", "#ff9800", "#ffc107"],
            "banners": ["PRODUTOS FRESCOS TODOS OS DIAS", "QUALIDADE QUE VOCÊ MERECE", "SABOR E NUTRIÇÃO"],
            "descricao_padrao": "Produto selecionado com qualidade e procedência.",
            "exemplos": ["Coca-Cola 2L", "Pão de Forma", "Carne Moída", "Frutas Selecionadas"]
        },
        "🛍️ Loja de Variedades": {
            "icone": "🛍️",
            "categorias_padrao": ["Casa", "Escritório", "Presentes", "Utilidades", "Brinquedos", "Festa"],
            "cores_sugeridas": ["#ff6f00", "#ffab00", "#ffd600"],
            "banners": ["TUDO PARA SUA CASA", "VARIEDADES COM QUALIDADE", "O MELHOR PREÇO"],
            "descricao_padrao": "Produto versátil e de qualidade para seu dia a dia.",
            "exemplos": ["Caneca Personalizada", "Kit de Canetas", "Velas Aromáticas", "Organizador de Mesa"]
        },
        "💄 Beleza e Estética": {
            "icone": "💄",
            "categorias_padrao": ["Maquiagem", "Perfumes", "Cuidados com a Pele", "Cabelos", "Unhas", "Barba"],
            "cores_sugeridas": ["#e91e63", "#ff4081", "#f06292"],
            "banners": ["BELEZA QUE ENCANTA", "PRODUTOS PREMIUM", "CUIDADOS QUE VOCÊ MERECE"],
            "descricao_padrao": "Produto de beleza com qualidade e procedência.",
            "exemplos": ["Base Líquida", "Perfume Importado", "Creme Anti-idade", "Kit Barba"]
        },
        "🏋️ Academia e Esportes": {
            "icone": "🏋️",
            "categorias_padrao": ["Suplementos", "Roupas Esportivas", "Acessórios", "Equipamentos", "Nutrição"],
            "cores_sugeridas": ["#d32f2f", "#f44336", "#ff5252"],
            "banners": ["TREINE COM QUALIDADE", "SUA EVOLUÇÃO É NOSSA PRIORIDADE", "ESPORTE E SAÚDE"],
            "descricao_padrao": "Produto para potencializar seu treino e performance.",
            "exemplos": ["Whey Protein", "Legging Fitness", "Cordas de Pular", "Luvas de Academia"]
        },
        "📚 Livros e Papelaria": {
            "icone": "📚",
            "categorias_padrao": ["Livros", "Cadernos", "Canetas", "Mochilas", "Materiais Escolares", "Presentes"],
            "cores_sugeridas": ["#1565c0", "#1976d2", "#1e88e5"],
            "banners": ["CONHECIMENTO É PODER", "MATERIAL ESCOLAR DE QUALIDADE", "LEITURA QUE INSPIRA"],
            "descricao_padrao": "Material de qualidade para seus estudos e conhecimento.",
            "exemplos": ["Livro Best-seller", "Caderno Universitário", "Caneta Tinteiro", "Mochila Escolar"]
        },
        "🎸 Instrumentos Musicais": {
            "icone": "🎸",
            "categorias_padrao": ["Violões", "Guitarras", "Baterias", "Teclados", "Acessórios", "Áudio"],
            "cores_sugeridas": ["#4a148c", "#6a1b9a", "#8e24aa"],
            "banners": ["MÚSICA É VIDA", "INSTRUMENTOS COM QUALIDADE", "SUA PAIXÃO EM CADA NOTA"],
            "descricao_padrao": "Instrumento musical com qualidade profissional.",
            "exemplos": ["Violão Yamaha", "Guitarra Fender", "Baqueta Profissional", "Pedal de Efeito"]
        },
        "🧸 Brinquedos e Infantil": {
            "icone": "🧸",
            "categorias_padrao": ["Brinquedos Educativos", "Bonecos", "Jogos", "Infantil", "Festa", "Montessori"],
            "cores_sugeridas": ["#e65100", "#f57c00", "#fb8c00"],
            "banners": ["DIVERSÃO E APRENDIZADO", "MOMENTOS ESPECIAIS", "BRINQUEDOS SEGUROS"],
            "descricao_padrao": "Brinquedo seguro e educativo para seu filho.",
            "exemplos": ["Quebra-cabeça", "Boneca Baby", "Jogo de Tabuleiro", "Kit de Massinha"]
        },
        "🌿 Jardinagem e Paisagismo": {
            "icone": "🌿",
            "categorias_padrao": ["Plantas", "Vasos", "Ferramentas", "Adubos", "Decoração", "Hortaliças"],
            "cores_sugeridas": ["#2e7d32", "#388e3c", "#43a047"],
            "banners": ["SEU JARDIM PERFEITO", "PLANTAS QUE ENCANTAM", "NATUREZA EM SUA CASA"],
            "descricao_padrao": "Produto de qualidade para seu jardim e plantas.",
            "exemplos": ["Orquídea Phalaenopsis", "Vaso Autoirrigável", "Tesoura de Poda", "Adubo Orgânico"]
        },
        "🔧 Ferramentas e Construção": {
            "icone": "🔧",
            "categorias_padrao": ["Ferramentas Manuais", "Elétricas", "Hidráulica", "Elétrica", "Pintura", "Segurança"],
            "cores_sugeridas": ["#bf360c", "#d84315", "#e64a19"],
            "banners": ["A FERRAMENTA CERTA", "CONSTRUÇÃO COM QUALIDADE", "EQUIPAMENTOS PROFISSIONAIS"],
            "descricao_padrao": "Ferramenta profissional com durabilidade e eficiência.",
            "exemplos": ["Furadeira Bosch", "Martelo Profissional", "Trena Digital", "Luvas de Segurança"]
        },
        "🎮 Games e Informática": {
            "icone": "🎮",
            "categorias_padrao": ["Games", "PC Gamer", "Acessórios", "Notebooks", "Periféricos", "Consoles"],
            "cores_sugeridas": ["#1a237e", "#283593", "#303f9f"],
            "banners": ["TECNOLOGIA PARA GAMERS", "PERFORMANCE EXTREMA", "O FUTURO DO GAMING"],
            "descricao_padrao": "Equipamento de alto desempenho para gamers.",
            "exemplos": ["Mouse Gamer", "Teclado Mecânico", "Headset 7.1", "Placa de Vídeo"]
        },
        "🚗 Automóveis e Peças": {
            "icone": "🚗",
            "categorias_padrao": ["Motor", "Suspensão", "Freios", "Pneus", "Elétrica", "Carroceria", "Acessórios"],
            "cores_sugeridas": ["#1a237e", "#0d47a1", "#1565c0"],
            "banners": ["PEÇAS ORIGINAIS", "SEU CARRO EM BOAS MÃOS", "MELHOR PREÇO DO MERCADO"],
            "descricao_padrao": "Peça original com garantia de fábrica.",
            "exemplos": ["Motor 1.0", "Pastilha de Freio", "Amortecedor Dianteiro", "Bateria 60Ah"]
        }
    }
    return configs.get(nicho_escolhido, configs["🏍️ Peças de Moto Usada"])


def gerar_arquivo_site(nova_config):
    nicho = nova_config.get("nicho", "🏍️ Peças de Moto Usada")
    config_nicho = obter_config_nicho(nicho)
    cor_hex = nova_config.get("cor_principal", "#ff5722")
    whatsapp_numero = nova_config.get("whatsapp_contato", "5528999999999")
    instagram_link = nova_config.get("instagram_url", "https://instagram.com")
    tema = nova_config.get("tema_site", "Escuro")
    banners = nova_config.get("banners", [])
    cnpj_info = nova_config.get("cnpj_empresa", "CNPJ: 00.000.000/0001-00")
    logo_url = nova_config.get("logo_url", "")
    if not banners or not banners[0].get("url"):
        banners = [
            {"url": "https://images.unsplash.com/photo-1558981403-c5f9899a28bc", "frase": config_nicho["banners"][0]},
            {"url": "https://images.unsplash.com/photo-1568772585407-9361f9bf3a87", "frase": config_nicho["banners"][1]},
            {"url": "https://images.unsplash.com/photo-1609630875176-b800c92cf03d", "frase": config_nicho["banners"][2]},
        ]
    if tema == "Claro":
        bg_body = "#f4f6f8"; bg_header = "#ffffff"; bg_card = "#ffffff"
        text_main = "#222222"; text_muted = "#666666"; border_color = "#e0e0e0"; input_bg = "#ffffff"
    else:
        bg_body = "#121212"; bg_header = "#1a1a1a"; bg_card = "#1a1a1a"
        text_main = "#f1f1f1"; text_muted = "#aaaaaa"; border_color = "#333333"; input_bg = "#121212"

    carousel_html = ""
    for i, banner in enumerate(banners):
        url = banner.get("url", "")
        active = "active" if i == 0 else ""
        carousel_html += f'<div class="carousel-slide {active}" style="background-image: url(\'{url}\');"></div>'

    LISTA_TXT_URL = "https://raw.githubusercontent.com/cloneey9090-netizen/tv/main/lista.txt"

    anuncio_html = f"""
<div id="banner-rotativo-simplyon" style="
    max-width: 1100px;
    margin: 5px auto;
    padding: 0 15px;
">
    <div id="banner-container" style="
    width: 100%;
    position: relative;
    padding-bottom: 20%;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 8px 25px rgba(0,0,0,0.4);
    border: 1px solid {border_color};
    background: #050505;
    max-height: 180px;
"></div>
</div>

<script>
(function() {{
    const LISTA_TXT_URL = "{LISTA_TXT_URL}";
    const container = document.getElementById('banner-container');
    let banners = [];
    let idx = 0;
    let timerId = null;

    async function carregarBanners() {{
        try {{
            const res = await fetch(LISTA_TXT_URL + "?t=" + new Date().getTime());
            const texto = await res.text();
            const linhas = texto.split('\\n');

            linhas.forEach(linha => {{
                const limpa = linha.trim();
                if (!limpa.startsWith('IMG=')) return;

                let dados = limpa.replace('IMG=', '').trim();
                let url, link = null;

                if (dados.includes('|')) {{
                    const partes = dados.split('|');
                    url = partes[0].trim();
                    link = partes[1].trim();
                }} else {{
                    url = dados;
                }}

                if (!url) return;

                const img = document.createElement('img');
                img.src = url;
                img.alt = 'Banner';
                img.style.cssText = `
                    position: absolute;
                    inset: 0;
                    width: 100%;
                    height: 100%;
                    object-fit: fill;
                    background: #000;
                    display: none;
                    border-radius: 12px;
                    transition: opacity 0.6s ease;
                    opacity: 0;
                `;

                if (link) {{
                    img.style.cursor = 'pointer';
                    img.onclick = () => window.open(link, '_blank');
                }}

                container.appendChild(img);
                banners.push(img);
            }});

            if (banners.length > 0) {{
                mostrarProximo();
                timerId = setInterval(mostrarProximo, 10000);
            }} else {{
                // Nenhum banner encontrado — esconde o container todo
                document.getElementById('banner-rotativo-simplyon').style.display = 'none';
            }}
        }} catch (e) {{
            console.warn('Banner rotativo: falha ao carregar lista.', e);
            // Falha silenciosa — esconde pra não deixar buraco no site
            const el = document.getElementById('banner-rotativo-simplyon');
            if (el) el.style.display = 'none';
        }}
    }}

    function mostrarProximo() {{
        banners.forEach(b => {{
            b.style.display = 'none';
            b.style.opacity = '0';
        }});
        const atual = banners[idx];
        atual.style.display = 'block';
        // Força reflow pra animação de fade funcionar
        void atual.offsetWidth;
        atual.style.opacity = '1';
        idx = (idx + 1) % banners.length;
    }}

    // Só carrega depois que o DOM estiver pronto
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', carregarBanners);
    }} else {{
        carregarBanners();
    }}
}})();
</script>
"""

    html_conteudo = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{nova_config.get('nome_loja', 'Loja')}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: {bg_body}; color: {text_main}; }}
        header {{ display: flex; justify-content: space-between; align-items: center; padding: 15px 20px; background: {bg_header}; border-bottom: 1px solid {border_color}; gap: 15px; flex-wrap: wrap; position: sticky; top: 0; z-index: 100; }}
        .logo-container {{ display: flex; align-items: center; }}
        .logo {{ max-height: 80px !important; width: auto; object-fit: contain; display: block; }}
        .search-header {{ flex: 1; max-width: 300px; min-width: 150px; }}
        .search-header input {{ width: 100%; padding: 8px 12px; border-radius: 6px; border: 1px solid {border_color}; background: {input_bg}; color: {text_main}; font-size: 0.9em; outline: none; }}
        .search-header input:focus {{ border-color: {cor_hex}; }}
        .header-actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
        .social-icons {{ display: flex; gap: 10px; align-items: center; }}
        .social-icons a {{ color: {text_muted}; font-size: 20px; text-decoration: none; transition: color 0.3s; }}
        .social-icons a.whatsapp:hover {{ color: #25d366; }}
        .social-icons a.instagram:hover {{ color: #e1306c; }}
        .btn-carrinho-topo {{ background: {cor_hex}; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: bold; display: flex; align-items: center; gap: 8px; font-size: 14px; transition: opacity 0.2s; }}
        .btn-carrinho-topo:hover {{ opacity: 0.85; }}
        .carousel-container {{ position: relative; width: 100%; height: auto; aspect-ratio: 16 / 9; overflow: hidden; }}
        .carousel-slide {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-size: cover; background-position: center; opacity: 0; transition: opacity 1.2s ease-in-out; }}
        .carousel-slide.active {{ opacity: 1; }}
        .carousel-slide::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.15); z-index: 1; }}
        .linha-destaque {{ height: 3px; background-color: {cor_hex}; width: 100%; }}
        .container {{ max-width: 1100px; margin: 30px auto; padding: 0 15px; min-height: 400px; }}
        h2 {{ font-size: 20px; text-transform: uppercase; letter-spacing: 1px; border-left: 4px solid {cor_hex}; padding-left: 10px; color: {text_main}; margin-bottom: 20px; }}
        .filtros-container {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; padding: 10px 0; border-bottom: 1px solid {border_color}; }}
        .filtro-btn {{ padding: 6px 14px; border: 2px solid {border_color}; border-radius: 25px; background: transparent; color: {text_muted}; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; text-transform: capitalize; }}
        .filtro-btn:hover, .filtro-btn.ativo {{ background: {cor_hex}; color: #fff; border-color: {cor_hex}; }}
        .filtro-btn .contagem {{ display: inline-block; background: rgba(255,255,255,0.2); border-radius: 12px; padding: 0 8px; font-size: 11px; margin-left: 5px; }}
        .filtro-btn.ativo .contagem {{ background: rgba(255,255,255,0.3); }}
        .grid-produtos {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 15px; }}
        .card {{ background: {bg_card}; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1); border: 1px solid {border_color}; display: flex; flex-direction: column; justify-content: space-between; transition: transform 0.2s; position: relative; }}
        .card:hover {{ transform: translateY(-4px); }}
        .card img {{ width: 100%; height: 150px; object-fit: cover; }}
        .badge-destaque {{ position: absolute; top: 10px; right: 10px; background: #ffd700; color: #000; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; z-index: 5; }}
        .card-body {{ padding: 12px; }}
        .categoria-tag {{ font-size: 11px; color: {cor_hex}; text-transform: uppercase; font-weight: bold; display: inline-block; margin-bottom: 4px; }}
        .card-title {{ margin: 4px 0 6px 0; font-size: 16px; color: {text_main}; font-weight: bold; }}
        .card-modelo, .card-desc {{ color: {text_muted}; font-size: 13px; margin-bottom: 3px; }}
        .card-footer {{ display: flex; justify-content: space-between; align-items: center; padding: 0 12px 12px 12px; }}
        .preco {{ font-size: 16px; font-weight: bold; color: #2ecc71; }}
        .btn-adicionar {{ background: {cor_hex}; color: #fff; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: bold; transition: opacity 0.2s; }}
        .btn-adicionar:hover {{ opacity: 0.85; }}
        .sem-produtos {{ color: {text_muted}; text-align: center; padding: 40px 20px; grid-column: 1/-1; }}
        .modal-carrinho {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 1000; justify-content: flex-end; }}
        .modal-conteudo {{ background: {bg_card}; width: 100%; max-width: 400px; height: 100%; padding: 25px; display: flex; flex-direction: column; justify-content: space-between; border-left: 1px solid {border_color}; animation: slideIn 0.3s ease; }}
        @keyframes slideIn {{ from {{ transform: translateX(100%); }} to {{ transform: translateX(0); }} }}
        .carrinho-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid {border_color}; padding-bottom: 15px; }}
        .carrinho-itens {{ flex: 1; overflow-y: auto; margin: 15px 0; }}
        .item-carrinho {{ display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid {border_color}; font-size: 14px; }}
        .btn-remover {{ color: #ff4d4d; background: none; border: none; cursor: pointer; font-size: 16px; }}
        .carrinho-footer {{ border-top: 1px solid {border_color}; padding-top: 15px; }}
        .btn-fechar-pedido {{ background: #25d366; color: #fff; width: 100%; padding: 12px; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; cursor: pointer; }}
        .btn-fechar-pedido:hover {{ background: #1ebd5b; }}
        footer {{ margin-top: 40px; padding: 30px 20px; background: {bg_header}; border-top: 1px solid {border_color}; text-align: center; color: {text_muted}; font-size: 13px; }}
        footer p {{ margin: 5px 0; }}
        @media (max-width: 600px) {{ header {{ padding: 10px 15px; }} .logo {{ max-height: 50px !important; }} .search-header {{ max-width: 160px; min-width: 100px; }} .btn-carrinho-topo {{ padding: 5px 10px; font-size: 11px; }} .filtros-container {{ gap: 5px; }} .filtro-btn {{ padding: 5px 10px; font-size: 11px; }} .grid-produtos {{ grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }} .card img {{ height: 120px; }} .carousel-container {{ aspect-ratio: 16 / 9; }} .card-title {{ font-size: 14px; }} .preco {{ font-size: 14px; }} }}
        @media (max-width: 400px) {{ .grid-produtos {{ grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); }} .card img {{ height: 100px; }} }}
    </style>
</head>
<body>
    <header>
        <div class="logo-container">
            {f'<img src="{logo_url}" class="logo" alt="Logo">' if logo_url else f'<h2 style="margin:0; border:none; padding:0; font-size:18px;">{nova_config.get("nome_loja")}</h2>'}
        </div>
        <div class="search-header">
            <input type="text" id="searchInput" placeholder="Buscar..." onkeyup="filtrarProdutos()">
        </div>
        <div class="header-actions">
            <button class="btn-carrinho-topo" onclick="abrirCarrinho()">
                <i class="fa-solid fa-cart-shopping"></i> (<span id="contadorCarrinho">0</span>)
            </button>
            <div class="social-icons">
                <a href="https://wa.me/{whatsapp_numero}" target="_blank" class="whatsapp"><i class="fa-brands fa-whatsapp"></i></a>
                <a href="{instagram_link}" target="_blank" class="instagram"><i class="fa-brands fa-instagram"></i></a>
            </div>
        </div>
    </header>
    <div class="carousel-container" id="carousel">{carousel_html}</div>
    <div class="linha-destaque"></div>
    {anuncio_html}
    <div class="container">
        <h2>Catálogo Disponível</h2>
        <div class="filtros-container" id="filtrosContainer"></div>
        <div class="grid-produtos" id="vitrine"></div>
    </div>
    <div class="modal-carrinho" id="modalCarrinho">
        <div class="modal-conteudo">
            <div class="carrinho-header">
                <h3 style="margin:0; color:{text_main};">Seu Carrinho</h3>
                <button onclick="fecharCarrinho()" style="background:none; border:none; color:{text_muted}; font-size:24px; cursor:pointer;"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <div class="carrinho-itens" id="listaCarrinho">
                <p style="color: {text_muted}; text-align: center; margin-top: 40px;">O carrinho está vazio.</p>
            </div>
            <div class="carrinho-footer">
                <button class="btn-fechar-pedido" onclick="enviarPedidoWhatsApp()">Enviar Pedido no WhatsApp</button>
            </div>
        </div>
    </div>
    <footer>
        <p><strong>{nova_config.get('nome_loja', 'Loja')}</strong> | {cnpj_info}</p>
        <p>Compromisso com a qualidade.</p>
    </footer>
    <script>
        let currentSlide = 0;
        const slides = document.querySelectorAll('.carousel-slide');
        if (slides.length > 1) {{
            setInterval(() => {{
                slides[currentSlide].classList.remove('active');
                currentSlide = (currentSlide + 1) % slides.length;
                slides[currentSlide].classList.add('active');
            }}, 5000);
        }}
        const numeroZap = "{whatsapp_numero}";
        let listaProdutos = {json.dumps(carregar_json(ARQUIVO_JSON, []), ensure_ascii=False)};
        let carrinho = [];
        let categoriaAtiva = 'todos';
        let termoBusca = '';
        function extrairCategorias(produtos) {{
            const categorias = new Set();
            categorias.add('todos');
            produtos.forEach(item => {{
                if (item.categoria && item.categoria.trim()) categorias.add(item.categoria.trim());
            }});
            return Array.from(categorias);
        }}
        function contarPorCategoria(produtos, categoria) {{
            if (categoria === 'todos') return produtos.length;
            return produtos.filter(item => item.categoria && item.categoria.trim() === categoria).length;
        }}
        function gerarBotoesFiltro(produtos) {{
            const container = document.getElementById('filtrosContainer');
            const categorias = extrairCategorias(produtos);
            let html = '';
            categorias.forEach(cat => {{
                const contagem = contarPorCategoria(produtos, cat);
                const isAtivo = (categoriaAtiva === cat) ? 'ativo' : '';
                const nomeExibicao = cat === 'todos' ? '📦 Todos' : cat;
                html += `<button class="filtro-btn ${{isAtivo}}" data-categoria="${{cat}}" onclick="filtrarPorCategoria('${{cat}}')">${{nomeExibicao}} <span class="contagem">${{contagem}}</span></button>`;
            }});
            container.innerHTML = html;
        }}
        function filtrarPorCategoria(categoria) {{
            categoriaAtiva = categoria;
            document.querySelectorAll('.filtro-btn').forEach(btn => btn.classList.toggle('ativo', btn.dataset.categoria === categoria));
            aplicarFiltros();
        }}
        function filtrarProdutos() {{
            termoBusca = document.getElementById('searchInput').value.toLowerCase();
            aplicarFiltros();
        }}
        function aplicarFiltros() {{
            const termo = termoBusca || document.getElementById('searchInput').value.toLowerCase();
            let produtosFiltrados = listaProdutos;
            if (categoriaAtiva !== 'todos') {{
                produtosFiltrados = produtosFiltrados.filter(item => item.categoria && item.categoria.trim() === categoriaAtiva);
            }}
            if (termo && termo.trim() !== '') {{
                produtosFiltrados = produtosFiltrados.filter(item =>
                    (item.nome && item.nome.toLowerCase().includes(termo)) ||
                    (item.modelo && item.modelo.toLowerCase().includes(termo)) ||
                    (item.descricao && item.descricao.toLowerCase().includes(termo)) ||
                    (item.categoria && item.categoria.toLowerCase().includes(termo))
                );
            }}
            exibirProdutos(produtosFiltrados);
        }}
        function exibirProdutos(produtos) {{
            const vitrine = document.getElementById('vitrine');
            vitrine.innerHTML = '';
            if (produtos.length === 0) {{
                vitrine.innerHTML = '<div class="sem-produtos">Nenhum produto encontrado.</div>';
                return;
            }}
            produtos.forEach(item => {{
                const destaqueBadge = item.destaque ? '<div class="badge-destaque">⭐ Destaque</div>' : '';
                vitrine.innerHTML += `
                    <div class="card">
                        <div>
                            <img src="${{item.imagem || 'https://images.unsplash.com/photo-1558981403-c5f9899a28bc'}}" alt="${{item.nome}}" onerror="this.src='https://images.unsplash.com/photo-1558981403-c5f9899a28bc'">
                            ${{destaqueBadge}}
                            <div class="card-body">
                                <span class="categoria-tag">${{item.categoria || 'Geral'}}</span>
                                <div class="card-title">${{item.nome}}</div>
                                ${{item.modelo ? `<div class="card-modelo">${{item.modelo}}</div>` : ''}}
                                ${{item.descricao ? `<div class="card-desc">${{item.descricao}}</div>` : ''}}
                            </div>
                        </div>
                        <div class="card-footer">
                            <span class="preco">${{item.preco || 'R$ 0,00'}}</span>
                            <button class="btn-adicionar" onclick="adicionarAoCarrinho('${{item.nome.replace(/'/g, "\\\\'")}}', '${{item.preco || 'R$ 0,00'}}')">Adicionar</button>
                        </div>
                    </div>
                `;
            }});
        }}
        function adicionarAoCarrinho(nome, preco) {{
            carrinho.push({{ nome, preco }});
            document.getElementById('contadorCarrinho').innerText = carrinho.length;
            atualizarCarrinhoUI();
            abrirCarrinho();
        }}
        function removerDoCarrinho(index) {{
            carrinho.splice(index, 1);
            document.getElementById('contadorCarrinho').innerText = carrinho.length;
            atualizarCarrinhoUI();
        }}
        function atualizarCarrinhoUI() {{
            const container = document.getElementById('listaCarrinho');
            if (carrinho.length === 0) {{
                container.innerHTML = '<p style="color: {text_muted}; text-align: center; margin-top: 40px;">O carrinho está vazio.</p>';
                return;
            }}
            container.innerHTML = '';
            carrinho.forEach((item, index) => {{
                container.innerHTML += `
                    <div class="item-carrinho">
                        <div>
                            <strong>${{item.nome}}</strong><br>
                            <span style="color: #2ecc71;">${{item.preco}}</span>
                        </div>
                        <button class="btn-remover" onclick="removerDoCarrinho(${{index}})"><i class="fa-solid fa-trash"></i></button>
                    </div>
                `;
            }});
        }}
        function abrirCarrinho() {{ document.getElementById('modalCarrinho').style.display = 'flex'; }}
        function fecharCarrinho() {{ document.getElementById('modalCarrinho').style.display = 'none'; }}
        function enviarPedidoWhatsApp() {{
            if (carrinho.length === 0) return;
            let texto = "Olá! Gostaria de fechar o seguinte pedido:%0A%0A";
            carrinho.forEach((item, i) => {{
                texto += `%23${{i+1}} - ${{item.nome}} (*${{item.preco}}*)%0A`;
            }});
            texto += "%0AConfirma a disponibilidade?";
            window.open(`https://wa.me/${{numeroZap}}?text=${{texto}}`, '_blank');
        }}
        gerarBotoesFiltro(listaProdutos);
        exibirProdutos(listaProdutos);
    </script>
</body>
</html>"""
    with open(ARQUIVO_HTML, "w", encoding="utf-8") as f:
        f.write(html_conteudo)
    disparar_servidor_em_segundo_plano()


# ============================================================
# ===== FUNÇÃO PRINCIPAL =====================================
# ============================================================
def main(page: ft.Page):
    global link_publico, tunel_ativo

    page.title = "SimplyON"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 480
    page.window.height = 720

    splash = ft.Container(
        expand=True,
        image=ft.DecorationImage(
            src="assets/splash.png",
            fit=ft.ImageFit.COVER,
        ),
        content=ft.Column([
            ft.Container(expand=True),
            ft.Container(
                content=ft.Column([
                    ft.Container(
                        content=ft.Text("Carregando...", size=16, color="white"),
                        bgcolor="#00000066",
                        padding=8,
                        border_radius=5,
                    ),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                alignment=ft.alignment.bottom_center,
            ),
        ]),
    )

    page.add(splash)
    page.update()

    def on_keyboard(e: ft.KeyboardEvent):
        if e.key == "Back":
            page.open(ft.SnackBar(content=ft.Text("🔄 Use o botão Home ou minimize para manter o site ativo.")))
            return True

    page.on_keyboard_event = on_keyboard

    def carregar_app_com_splash():
        import time as _time
        _time.sleep(2)

        config = carregar_json(ARQUIVO_CONFIG, {
            "nome_loja": "Sua Loja",
            "subtitulo": "Catálogo de Produtos",
            "cnpj_empresa": "CNPJ: 00.000.000/0001-00",
            "cor_principal": "#ff5722",
            "logo_url": "",
            "banners": [
                {"url": "", "frase": "QUALIDADE E PROCEDÊNCIA EM CADA PEÇA"},
                {"url": "", "frase": "AS MELHORES MARCAS PARA VOCÊ"},
                {"url": "", "frase": "ATENDIMENTO ESPECIALIZADO E RÁPIDO"}
            ],
            "whatsapp_contato": "5528999999999",
            "instagram_url": "https://instagram.com",
            "tema_site": "Escuro",
            "nicho": "🏍️ Peças de Moto Usada"
        })

        estoque = carregar_json(ARQUIVO_JSON, [])

        txt_nome = ft.TextField(label="Nome da Peça")
        txt_modelo = ft.TextField(label="Modelo")
        txt_categoria = ft.TextField(label="Categoria")
        txt_preco = ft.TextField(label="Preço (Ex: R$ 150,00)")
        txt_desc = ft.TextField(label="Descrição")
        txt_destaque = ft.Dropdown(label="Produto Destaque?", value="Não",
                                    options=[ft.dropdown.Option("Não"), ft.dropdown.Option("Sim")])

        caminho_imagem_selecionada = ""
        txt_imagem_nome = ft.Text("📷 Nenhuma imagem selecionada", size=12, color="#888")

        def on_imagem_selecionada(e: ft.FilePickerResultEvent):
            nonlocal caminho_imagem_selecionada
            if e.files:
                caminho_imagem_selecionada = e.files[0].path
                nome_arquivo = os.path.basename(caminho_imagem_selecionada)
                txt_imagem_nome.value = f"📷 {nome_arquivo}"
                page.update()

        file_picker_imagem = ft.FilePicker()
        file_picker_imagem.on_result = on_imagem_selecionada
        page.overlay.append(file_picker_imagem)

        def selecionar_imagem_click(e):
            file_picker_imagem.pick_files(
                allow_multiple=False,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]
            )
        btn_selecionar_imagem = ft.ElevatedButton(
            "📁 Selecionar Imagem",
            on_click=selecionar_imagem_click,
            icon=ft.Icons.FOLDER_OPEN
        )

        caminho_logo_selecionada = ""
        txt_logo_nome = ft.Text("📷 Nenhuma logo selecionada", size=12, color="#888")

        def on_logo_selecionada(e: ft.FilePickerResultEvent):
            nonlocal caminho_logo_selecionada
            if e.files:
                caminho_logo_selecionada = e.files[0].path
                txt_logo_nome.value = f"📷 {os.path.basename(caminho_logo_selecionada)}"
                page.update()

        file_picker_logo = ft.FilePicker()
        file_picker_logo.on_result = on_logo_selecionada
        page.overlay.append(file_picker_logo)

        def selecionar_logo_click(e):
            file_picker_logo.pick_files(
                allow_multiple=False,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]
            )
        btn_selecionar_logo = ft.ElevatedButton(
            "📁 Selecionar Logo",
            on_click=selecionar_logo_click,
            icon=ft.Icons.FOLDER_OPEN
        )

        caminho_banner1_selecionado = ""
        txt_banner1_nome = ft.Text("📷 Nenhum banner 1 selecionado", size=12, color="#888")

        def on_banner1_selecionado(e: ft.FilePickerResultEvent):
            nonlocal caminho_banner1_selecionado
            if e.files:
                caminho_banner1_selecionado = e.files[0].path
                txt_banner1_nome.value = f"📷 {os.path.basename(caminho_banner1_selecionado)}"
                page.update()

        file_picker_banner1 = ft.FilePicker()
        file_picker_banner1.on_result = on_banner1_selecionado
        page.overlay.append(file_picker_banner1)

        def selecionar_banner1_click(e):
            file_picker_banner1.pick_files(
                allow_multiple=False,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]
            )
        btn_selecionar_banner1 = ft.ElevatedButton(
            "📁 Selecionar Banner 1",
            on_click=selecionar_banner1_click,
            icon=ft.Icons.FOLDER_OPEN
        )

        caminho_banner2_selecionado = ""
        txt_banner2_nome = ft.Text("📷 Nenhum banner 2 selecionado", size=12, color="#888")

        def on_banner2_selecionado(e: ft.FilePickerResultEvent):
            nonlocal caminho_banner2_selecionado
            if e.files:
                caminho_banner2_selecionado = e.files[0].path
                txt_banner2_nome.value = f"📷 {os.path.basename(caminho_banner2_selecionado)}"
                page.update()

        file_picker_banner2 = ft.FilePicker()
        file_picker_banner2.on_result = on_banner2_selecionado
        page.overlay.append(file_picker_banner2)

        def selecionar_banner2_click(e):
            file_picker_banner2.pick_files(
                allow_multiple=False,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]
            )
        btn_selecionar_banner2 = ft.ElevatedButton(
            "📁 Selecionar Banner 2",
            on_click=selecionar_banner2_click,
            icon=ft.Icons.FOLDER_OPEN
        )

        caminho_banner3_selecionado = ""
        txt_banner3_nome = ft.Text("📷 Nenhum banner 3 selecionado", size=12, color="#888")

        def on_banner3_selecionado(e: ft.FilePickerResultEvent):
            nonlocal caminho_banner3_selecionado
            if e.files:
                caminho_banner3_selecionado = e.files[0].path
                txt_banner3_nome.value = f"📷 {os.path.basename(caminho_banner3_selecionado)}"
                page.update()

        file_picker_banner3 = ft.FilePicker()
        file_picker_banner3.on_result = on_banner3_selecionado
        page.overlay.append(file_picker_banner3)

        def selecionar_banner3_click(e):
            file_picker_banner3.pick_files(
                allow_multiple=False,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]
            )
        btn_selecionar_banner3 = ft.ElevatedButton(
            "📁 Selecionar Banner 3",
            on_click=selecionar_banner3_click,
            icon=ft.Icons.FOLDER_OPEN
        )

        lista_estoque = ft.Column()

        cores_disponiveis = {
            "Vermelho Cinematográfico": "#ff5722",
            "Azul Profissional": "#2196f3",
            "Verde PetShop": "#4caf50",
            "Preto Minimalista": "#000000",
            "Dourado Premium": "#ffd700",
            "Rosa Chique": "#e91e63",
            "Roxo Criativo": "#9c27b0",
            "Laranja Vibrante": "#ff9800",
            "Cinza Elegante": "#607d8b",
            "Marrom Conforto": "#795548"
        }

        nicho_opcoes = [
            "🏍️ Peças de Moto Usada", "🐶 PetShop / Animais", "🚲 Bicicletas / Bike",
            "📱 Eletrônicos / Celulares", "👗 Moda / Roupas", "🛋️ Móveis / Decoração",
            "🍔 Alimentação / Mercado", "🛍️ Loja de Variedades", "💄 Beleza e Estética",
            "🏋️ Academia e Esportes", "📚 Livros e Papelaria", "🎸 Instrumentos Musicais",
            "🧸 Brinquedos e Infantil", "🌿 Jardinagem e Paisagismo",
            "🔧 Ferramentas e Construção", "🎮 Games e Informática", "🚗 Automóveis e Peças"
        ]

        dropdown_nicho = ft.Dropdown(
            label="📌 Tipo de Comércio",
            value=config.get("nicho", "🏍️ Peças de Moto Usada"),
            options=[ft.dropdown.Option(opcao) for opcao in nicho_opcoes],
            on_change=lambda e: aplicar_nicho(e.control.value)
        )

        def aplicar_nicho(nicho_escolhido):
            config_nicho = obter_config_nicho(nicho_escolhido)
            txt_categoria.hint_text = f"Ex: {', '.join(config_nicho['categorias_padrao'][:4])}"
            txt_desc.hint_text = f"Ex: {config_nicho['descricao_padrao']}"
            config["nicho"] = nicho_escolhido
            salvar_json(ARQUIVO_CONFIG, config)
            page.open(ft.SnackBar(content=ft.Text(f"✅ Configurações para {nicho_escolhido} aplicadas!")))
            page.update()

        def atualizar_lista():
            lista_estoque.controls.clear()
            for item in estoque:
                destaque_texto = " ⭐" if item.get("destaque", False) else ""
                lista_estoque.controls.append(
                    ft.ListTile(
                        title=ft.Text(f"{item['nome']}{destaque_texto}", weight=ft.FontWeight.BOLD),
                        subtitle=ft.Text(f"{item['modelo']} - {item['preco']}"),
                        trailing=ft.IconButton(
                            icon=ft.Icons.DELETE,
                            icon_color="red",
                            on_click=lambda e, id_item=item["id"]: remover_peca(id_item)
                        )
                    )
                )

        def remover_peca(id_peca):
            nonlocal estoque
            estoque = [item for item in estoque if item["id"] != id_peca]
            salvar_json(ARQUIVO_JSON, estoque)
            gerar_arquivo_site(config)
            atualizar_lista()
            page.open(ft.SnackBar(content=ft.Text("Item removido com sucesso!")))
            page.update()

        def salvar_peca(e):
            nonlocal estoque, caminho_imagem_selecionada
            item_novo = {
                "id": len(estoque) + 1 if not estoque else max(item["id"] for item in estoque) + 1,
                "nome": txt_nome.value,
                "modelo": txt_modelo.value,
                "categoria": txt_categoria.value,
                "status": "Disponível",
                "preco": txt_preco.value,
                "descricao": txt_desc.value,
                "destaque": txt_destaque.value == "Sim"
            }
            imagem_final = "https://images.unsplash.com/photo-1558981403-c5f9899a28bc"
            if caminho_imagem_selecionada and os.path.exists(caminho_imagem_selecionada):
                try:
                    if not os.path.exists(PASTA_IMAGENS):
                        os.makedirs(PASTA_IMAGENS)
                    extensao = os.path.splitext(caminho_imagem_selecionada)[1]
                    timestamp = int(time.time())
                    nome_arquivo = f"produto_{item_novo['id']}_{timestamp}{extensao}"
                    destino = os.path.join(PASTA_IMAGENS, nome_arquivo)
                    shutil.copy2(caminho_imagem_selecionada, destino)
                    imagem_final = f"imagens/{nome_arquivo}"
                    caminho_imagem_selecionada = ""
                    txt_imagem_nome.value = "📷 Nenhuma imagem selecionada"
                except Exception as ex:
                    print(f"Erro ao copiar imagem: {ex}")
                    imagem_final = "https://images.unsplash.com/photo-1558981403-c5f9899a28bc"
            item_novo["imagem"] = imagem_final
            estoque.append(item_novo)
            salvar_json(ARQUIVO_JSON, estoque)
            gerar_arquivo_site(config)
            txt_nome.value = ""
            txt_modelo.value = ""
            txt_categoria.value = ""
            txt_preco.value = ""
            txt_desc.value = ""
            txt_destaque.value = "Não"
            txt_imagem_nome.value = "📷 Nenhuma imagem selecionada"
            atualizar_lista()
            page.open(ft.SnackBar(content=ft.Text("Item cadastrado e site atualizado com sucesso!")))
            page.update()

        def importar_planilha_result(e: ft.FilePickerResultEvent):
            nonlocal estoque
            if not e.files:
                return
            caminho_arquivo = e.files[0].path
            try:
                if caminho_arquivo.endswith('.csv'):
                    df = pd.read_csv(caminho_arquivo, sep=';', encoding='utf-8-sig')
                else:
                    df = pd.read_excel(caminho_arquivo, engine='openpyxl')
                col_nome = next((c for c in df.columns if c in ['nome', 'produto', 'titulo', 'item']), None)
                col_modelo = next((c for c in df.columns if c in ['modelo', 'versao', 'codigo']), None)
                col_categoria = next((c for c in df.columns if c in ['categoria', 'grupo', 'setor']), None)
                col_preco = next((c for c in df.columns if c in ['preço', 'preco', 'valor', 'venda']), None)
                col_desc = next((c for c in df.columns if c in ['descrição', 'descricao', 'detalhes']), None)
                col_imagem = next((c for c in df.columns if c in ['imagem', 'foto', 'img', 'link']), None)
                if not col_nome or not col_preco:
                    page.open(ft.SnackBar(content=ft.Text("Erro: A planilha precisa ter colunas 'Nome' e 'Preço'.")))
                    page.update()
                    return
                proximo_id = len(estoque) + 1 if not estoque else max(item["id"] for item in estoque) + 1
                novos_itens = 0
                for _, row in df.iterrows():
                    nome_val = str(row[col_nome]) if pd.notna(row[col_nome]) else ""
                    if not nome_val or nome_val.lower() == 'nan':
                        continue
                    imagem_url = "https://images.unsplash.com/photo-1558981403-c5f9899a28bc"
                    if col_imagem and pd.notna(row[col_imagem]):
                        imagem_url = str(row[col_imagem])
                    item = {
                        "id": proximo_id, "nome": nome_val,
                        "modelo": str(row[col_modelo]) if col_modelo and pd.notna(row[col_modelo]) else "Padrão",
                        "categoria": str(row[col_categoria]) if col_categoria and pd.notna(row[col_categoria]) else "Geral",
                        "status": "Disponível",
                        "preco": str(row[col_preco]) if pd.notna(row[col_preco]) else "R$ 0,00",
                        "descricao": str(row[col_desc]) if col_desc and pd.notna(row[col_desc]) else "",
                        "imagem": imagem_url, "destaque": False
                    }
                    estoque.append(item)
                    proximo_id += 1
                    novos_itens += 1
                salvar_json(ARQUIVO_JSON, estoque)
                gerar_arquivo_site(config)
                atualizar_lista()
                page.open(ft.SnackBar(content=ft.Text(f"✅ {novos_itens} itens importados!")))
                page.update()
            except Exception as ex:
                page.open(ft.SnackBar(content=ft.Text(f"❌ Erro: {str(ex)}")))
                page.update()

        def carregar_config_upload():
            padrao = {"servico": "Netlify", "token": "", "site_name": "", "github_repo": ""}
            if not os.path.exists(ARQUIVO_UPLOAD_CONFIG):
                with open(ARQUIVO_UPLOAD_CONFIG, "w", encoding="utf-8") as f:
                    json.dump(padrao, f, indent=2)
                return padrao
            with open(ARQUIVO_UPLOAD_CONFIG, "r", encoding="utf-8") as f:
                return json.load(f)

        def salvar_config_upload(token, site_name, servico, github_repo):
            config_data = {"servico": servico, "token": token, "site_name": site_name, "github_repo": github_repo}
            with open(ARQUIVO_UPLOAD_CONFIG, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            return True

        def testar_conexao_netlify(token):
            try:
                headers = {"Authorization": f"Bearer {token}"}
                response = requests.get("https://api.netlify.com/api/v1/sites", headers=headers, timeout=10)
                return response.status_code == 200
            except:
                return False

        def testar_conexao_github(token):
            try:
                headers = {"Authorization": f"token {token}"}
                response = requests.get("https://api.github.com/user", headers=headers, timeout=10)
                return response.status_code == 200
            except:
                return False

        def hospedar_netlify(pasta_do_site, token, site_name):
            try:
                if not token:
                    return None, "Token não configurado"
                headers = {"Authorization": f"Bearer {token}"}
                url_sites = "https://api.netlify.com/api/v1/sites"
                response = requests.get(url_sites, headers=headers)
                if response.status_code != 200:
                    return None, f"Erro ao listar sites: {response.status_code}"
                sites = response.json()
                site_id = None
                for site in sites:
                    if site.get("name") == site_name:
                        site_id = site["id"]
                        break
                if not site_id:
                    create_data = {"name": site_name}
                    response = requests.post(url_sites, headers=headers, json=create_data)
                    if response.status_code in [200, 201]:
                        site_id = response.json()["id"]
                    else:
                        return None, f"Erro ao criar site: {response.status_code}"
                if not site_id:
                    return None, "Não foi possível obter o site_id."
                index_path = os.path.join(pasta_do_site, "index.html")
                if not os.path.exists(index_path):
                    return None, "Arquivo index.html não encontrado"
                zip_path = os.path.join(pasta_do_site, "deploy.zip")
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    zipf.write(index_path, arcname="index.html")
                    imagens_path = os.path.join(pasta_do_site, "imagens")
                    if os.path.exists(imagens_path):
                        for root, dirs, files in os.walk(imagens_path):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, pasta_do_site)
                                zipf.write(file_path, arcname=arcname)
                deploy_url = f"https://api.netlify.com/api/v1/sites/{site_id}/deploys"
                with open(zip_path, "rb") as zip_file:
                    zip_data = zip_file.read()
                headers_deploy = {"Authorization": f"Bearer {token}", "Content-Type": "application/zip"}
                response = requests.post(deploy_url, headers=headers_deploy, data=zip_data)
                if os.path.exists(zip_path):
                    os.remove(zip_path)
                if response.status_code not in [200, 201, 202]:
                    return None, f"Erro no deploy: {response.status_code}"
                deploy_data = response.json()
                url = deploy_data.get("ssl_url") or deploy_data.get("url")
                if not url:
                    site_info = requests.get(f"{url_sites}/{site_id}", headers=headers).json()
                    url = site_info.get("ssl_url") or site_info.get("url")
                if not url:
                    return None, "Deploy feito, mas link não retornado."
                return url, None
            except Exception as e:
                return None, f"Erro ao hospedar: {str(e)}"

        def hospedar_github(pasta_do_site, token, repo_nome):
            try:
                if not token or not repo_nome:
                    return None, "Token ou repositório não configurado"
                with open(ARQUIVO_HTML, "rb") as f:
                    conteudo = f.read()
                conteudo_base64 = base64.b64encode(conteudo).decode('utf-8')
                headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
                url_check = f"https://api.github.com/repos/{repo_nome}/contents/index.html"
                response_check = requests.get(url_check, headers=headers)
                if response_check.status_code == 200:
                    sha = response_check.json()["sha"]
                    data = {"message": "Site atualizado automaticamente", "content": conteudo_base64, "sha": sha, "branch": "main"}
                    response = requests.put(url_check, headers=headers, json=data)
                else:
                    data = {"message": "Site criado automaticamente", "content": conteudo_base64, "branch": "main"}
                    response = requests.put(url_check, headers=headers, json=data)
                if response.status_code in [200, 201]:
                    url = f"https://{repo_nome.split('/')[0]}.github.io/{repo_nome.split('/')[1]}"
                    return url, None
                else:
                    return None, f"Erro ao enviar para GitHub: {response.status_code}"
            except Exception as e:
                return None, f"Erro ao hospedar: {str(e)}"

        file_picker = ft.FilePicker()
        file_picker.on_result = importar_planilha_result
        page.overlay.append(file_picker)

        txt_nome_loja = ft.TextField(label="Nome da Loja", value=config.get("nome_loja", ""))
        txt_cnpj = ft.TextField(label="CNPJ ou Identificação", value=config.get("cnpj_empresa", ""))
        txt_whatsapp = ft.TextField(label="WhatsApp (Ex: 5528999999999)", value=config.get("whatsapp_contato", ""))
        txt_instagram = ft.TextField(label="Link do Instagram", value=config.get("instagram_url", ""))

        cor_atual_nome = "Vermelho Cinematográfico"
        for nome, codigo in cores_disponiveis.items():
            if codigo == config.get("cor_principal", "#ff5722"):
                cor_atual_nome = nome
                break

        dropdown_cor = ft.Dropdown(
            label="Cor Principal",
            value=cor_atual_nome,
            options=[ft.dropdown.Option(nome) for nome in cores_disponiveis.keys()]
        )
        dropdown_tema = ft.Dropdown(
            label="Tema do Site",
            value=config.get("tema_site", "Escuro"),
            options=[ft.dropdown.Option("Escuro"), ft.dropdown.Option("Claro")]
        )

        txt_token = ft.TextField(
            label="🔑 Token de Acesso",
            hint_text="Cole aqui o token gerado no Netlify ou GitHub",
            password=True, width=400
        )
        txt_nome_site = ft.TextField(
            label="📝 Nome do Site (Netlify)",
            hint_text="Ex: vitrine", width=400
        )
        txt_github_repo = ft.TextField(
            label="📂 Repositório GitHub",
            hint_text="Ex: usuario/repositorio", width=400, visible=False
        )
        dropdown_servico_hospedagem = ft.Dropdown(
            label="🌐 Serviço de Hospedagem",
            value="Netlify",
            options=[ft.dropdown.Option("Netlify"), ft.dropdown.Option("GitHub")],
            width=400,
            on_change=lambda e: mostrar_github(e.control.value)
        )

        def mostrar_github(servico):
            if servico == "GitHub":
                txt_github_repo.visible = True
                txt_nome_site.visible = False
            else:
                txt_github_repo.visible = False
                txt_nome_site.visible = True
            page.update()

        txt_status_hospedagem = ft.Text("⚪ Aguardando configuração...", size=12, color="#888")

        def testar_conexao_click(e):
            token = txt_token.value
            servico = dropdown_servico_hospedagem.value
            if not token:
                txt_status_hospedagem.value = "❌ Por favor, cole seu token"
                txt_status_hospedagem.color = "#ff5722"
                page.update()
                return
            if servico == "Netlify":
                if testar_conexao_netlify(token):
                    txt_status_hospedagem.value = "✅ Conexão com Netlify funcionando!"
                    txt_status_hospedagem.color = "#4caf50"
                else:
                    txt_status_hospedagem.value = "❌ Token inválido!"
                    txt_status_hospedagem.color = "#ff5722"
            else:
                if testar_conexao_github(token):
                    txt_status_hospedagem.value = "✅ Conexão com GitHub funcionando!"
                    txt_status_hospedagem.color = "#4caf50"
                else:
                    txt_status_hospedagem.value = "❌ Token inválido!"
                    txt_status_hospedagem.color = "#ff5722"
            page.update()

        def salvar_config_upload_click(e):
            token = txt_token.value
            site_name = txt_nome_site.value or "meu-site"
            servico = dropdown_servico_hospedagem.value
            github_repo = txt_github_repo.value or ""
            if not token:
                txt_status_hospedagem.value = "❌ Token é obrigatório!"
                txt_status_hospedagem.color = "#ff5722"
                page.update()
                return
            if servico == "GitHub" and not github_repo:
                txt_status_hospedagem.value = "❌ Repositório GitHub é obrigatório!"
                txt_status_hospedagem.color = "#ff5722"
                page.update()
                return
            salvar_config_upload(token, site_name, servico, github_repo)
            txt_status_hospedagem.value = f"✅ Configurações para {servico} salvas!"
            txt_status_hospedagem.color = "#4caf50"
            page.update()

        def hospedar_site_click(e):
            config_upload = carregar_config_upload()
            token = config_upload.get("token", "")
            servico = config_upload.get("servico", "Netlify")
            if not token:
                txt_status_hospedagem.value = "❌ Token não configurado!"
                txt_status_hospedagem.color = "#ff5722"
                page.update()
                return
            if not os.path.exists(ARQUIVO_HTML):
                txt_status_hospedagem.value = "❌ Site não gerado!"
                txt_status_hospedagem.color = "#ff5722"
                page.update()
                return
            txt_status_hospedagem.value = "⏳ Hospedando site... Aguarde..."
            txt_status_hospedagem.color = "#ff9800"
            page.update()
            if servico == "Netlify":
                site_name = config_upload.get("site_name", "meu-site")
                # ✅ CORRIGIDO: usar PASTA_DADOS
                url, erro = hospedar_netlify(PASTA_DADOS, token, site_name)
            else:
                repo = config_upload.get("github_repo", "")
                if not repo:
                    txt_status_hospedagem.value = "❌ Repositório GitHub não configurado!"
                    txt_status_hospedagem.color = "#ff5722"
                    page.update()
                    return
                # ✅ CORRIGIDO: usar PASTA_DADOS
                url, erro = hospedar_github(PASTA_DADOS, token, repo)
            if url:
                txt_status_hospedagem.value = f"✅ Site hospedado: {url}"
                txt_status_hospedagem.color = "#4caf50"
                mostrar_link(url)
                webbrowser.open(url)
            else:
                txt_status_hospedagem.value = f"❌ {erro}"
                txt_status_hospedagem.color = "#ff5722"
            page.update()

        config_upload = carregar_config_upload()
        if config_upload.get("token"):
            txt_token.value = config_upload["token"]
            txt_nome_site.value = config_upload.get("site_name", "")
            txt_github_repo.value = config_upload.get("github_repo", "")
            dropdown_servico_hospedagem.value = config_upload.get("servico", "Netlify")
            mostrar_github(dropdown_servico_hospedagem.value)
            txt_status_hospedagem.value = "✅ Configuração carregada!"
            txt_status_hospedagem.color = "#4caf50"

        link_text = ft.Text("Nenhum link gerado ainda", expand=True)
        link_exibicao = ft.Container(
            content=ft.Column([
                ft.Text("🔗 Link Público:", weight=ft.FontWeight.BOLD, size=14),
                ft.Row([
                    link_text,
                    ft.IconButton(
                        icon=ft.Icons.COPY,
                        tooltip="Copiar link",
                        on_click=lambda e: copiar_link(e),
                        disabled=True
                    )
                ])
            ]),
            padding=10, bgcolor="#1e1e1e", border_radius=6,
            margin=ft.margin.only(top=10), visible=False
        )

        def mostrar_link(link):
            link_text.value = link
            link_exibicao.content.controls[1].controls[1].disabled = False
            link_exibicao.visible = True
            page.update()

        def copiar_link(e):
            texto = link_text.value
            if texto and "Nenhum link" not in texto and "Verifique" not in texto:
                page.set_clipboard(texto)
                page.open(ft.SnackBar(content=ft.Text("✅ Link copiado!")))
                page.update()

        def abrir_site_local_click(e):
            global link_publico, tunel_ativo
            if not os.path.exists(ARQUIVO_HTML):
                page.open(ft.SnackBar(content=ft.Text("❌ Gere o site primeiro!")))
                page.update()
                return

            disparar_servidor_em_segundo_plano()

            page.open(ft.SnackBar(content=ft.Text("⏳ Criando túnel público, aguarde...")))
            page.update()

            def criar_tunel():
                global link_publico, tunel_ativo
                if not tunel_ativo:
                    mensagem = iniciar_tunel_pytunnel(8550)
                    page.open(ft.SnackBar(content=ft.Text(mensagem)))
                    page.update()
                if tunel_ativo and link_publico:
                    mostrar_link(link_publico)
                else:
                    ip = obter_ip_local()
                    link_publico = f"http://{ip}:8550"
                    mostrar_link(link_publico)
                page.update()

            threading.Thread(target=criar_tunel, daemon=True).start()

        btn_abrir_site_local = ft.ElevatedButton(
            text="📤 Compartilhar Meu Catálogo",
            on_click=abrir_site_local_click,
            icon=ft.Icons.SHARE,
            width=200
        )

        def salvar_config(e):
            nonlocal config
            nonlocal caminho_logo_selecionada
            nonlocal caminho_banner1_selecionado
            nonlocal caminho_banner2_selecionado
            nonlocal caminho_banner3_selecionado

            cor_selecionada = dropdown_cor.value
            logo_final = config.get("logo_url", "")

            if caminho_logo_selecionada and os.path.exists(caminho_logo_selecionada):
                try:
                    if not os.path.exists(PASTA_IMAGENS):
                        os.makedirs(PASTA_IMAGENS)
                    extensao = os.path.splitext(caminho_logo_selecionada)[1]
                    timestamp = int(time.time())
                    novo_nome = f"logo_{timestamp}{extensao}"
                    destino = os.path.join(PASTA_IMAGENS, novo_nome)
                    shutil.copy2(caminho_logo_selecionada, destino)
                    logo_antiga = config.get("logo_url", "")
                    if logo_antiga and "imagens/" in logo_antiga:
                        caminho_antigo = os.path.join(PASTA_DADOS, logo_antiga)
                        if os.path.exists(caminho_antigo) and caminho_antigo != destino:
                            try:
                                os.remove(caminho_antigo)
                            except:
                                pass
                    logo_final = f"imagens/{novo_nome}"
                    caminho_logo_selecionada = ""
                    txt_logo_nome.value = "📷 Nenhuma logo selecionada"
                except Exception as ex:
                    print(f"Erro ao copiar logo: {ex}")

            banner1_final = ""
            if caminho_banner1_selecionado and os.path.exists(caminho_banner1_selecionado):
                try:
                    extensao = os.path.splitext(caminho_banner1_selecionado)[1]
                    timestamp = int(time.time())
                    novo_nome = f"banner1_{timestamp}{extensao}"
                    destino = os.path.join(PASTA_IMAGENS, novo_nome)
                    shutil.copy2(caminho_banner1_selecionado, destino)
                    banner1_final = f"imagens/{novo_nome}"
                    caminho_banner1_selecionado = ""
                    txt_banner1_nome.value = "📷 Nenhum banner 1 selecionado"
                except Exception as ex:
                    print(f"Erro ao copiar banner 1: {ex}")
            else:
                banner1_final = config.get("banners", [{"url": ""}])[0].get("url", "") if config.get("banners") else ""

            banner2_final = ""
            if caminho_banner2_selecionado and os.path.exists(caminho_banner2_selecionado):
                try:
                    extensao = os.path.splitext(caminho_banner2_selecionado)[1]
                    timestamp = int(time.time())
                    novo_nome = f"banner2_{timestamp}{extensao}"
                    destino = os.path.join(PASTA_IMAGENS, novo_nome)
                    shutil.copy2(caminho_banner2_selecionado, destino)
                    banner2_final = f"imagens/{novo_nome}"
                    caminho_banner2_selecionado = ""
                    txt_banner2_nome.value = "📷 Nenhum banner 2 selecionado"
                except Exception as ex:
                    print(f"Erro ao copiar banner 2: {ex}")
            else:
                banner2_final = config.get("banners", [{"url": ""}, {"url": ""}])[1].get("url", "") if len(config.get("banners", [])) > 1 else ""

            banner3_final = ""
            if caminho_banner3_selecionado and os.path.exists(caminho_banner3_selecionado):
                try:
                    extensao = os.path.splitext(caminho_banner3_selecionado)[1]
                    timestamp = int(time.time())
                    novo_nome = f"banner3_{timestamp}{extensao}"
                    destino = os.path.join(PASTA_IMAGENS, novo_nome)
                    shutil.copy2(caminho_banner3_selecionado, destino)
                    banner3_final = f"imagens/{novo_nome}"
                    caminho_banner3_selecionado = ""
                    txt_banner3_nome.value = "📷 Nenhum banner 3 selecionado"
                except Exception as ex:
                    print(f"Erro ao copiar banner 3: {ex}")
            else:
                banner3_final = config.get("banners", [{"url": ""}, {"url": ""}, {"url": ""}])[2].get("url", "") if len(config.get("banners", [])) > 2 else ""

            frases = config.get("banners", [
                {"frase": "QUALIDADE E PROCEDÊNCIA"},
                {"frase": "AS MELHORES MARCAS PARA VOCÊ"},
                {"frase": "ATENDIMENTO ESPECIALIZADO"}
            ])
            banners = []
            if banner1_final:
                banners.append({"url": banner1_final, "frase": frases[0].get("frase", "Banner 1") if len(frases) > 0 else "Banner 1"})
            if banner2_final:
                banners.append({"url": banner2_final, "frase": frases[1].get("frase", "Banner 2") if len(frases) > 1 else "Banner 2"})
            if banner3_final:
                banners.append({"url": banner3_final, "frase": frases[2].get("frase", "Banner 3") if len(frases) > 2 else "Banner 3"})
            if not banners:
                config_nicho = obter_config_nicho(dropdown_nicho.value)
                banners = [
                    {"url": "https://images.unsplash.com/photo-1558981403-c5f9899a28bc", "frase": config_nicho["banners"][0]},
                    {"url": "https://images.unsplash.com/photo-1568772585407-9361f9bf3a87", "frase": config_nicho["banners"][1]},
                    {"url": "https://images.unsplash.com/photo-1609630875176-b800c92cf03d", "frase": config_nicho["banners"][2]}
                ]

            config = {
                "nome_loja": txt_nome_loja.value,
                "subtitulo": config.get("subtitulo", ""),
                "cnpj_empresa": txt_cnpj.value,
                "cor_principal": cores_disponiveis.get(cor_selecionada, "#ff5722"),
                "logo_url": logo_final,
                "banners": banners,
                "whatsapp_contato": txt_whatsapp.value,
                "instagram_url": txt_instagram.value,
                "tema_site": dropdown_tema.value,
                "nicho": dropdown_nicho.value
            }
            salvar_json(ARQUIVO_CONFIG, config)
            gerar_arquivo_site(config)
            page.open(ft.SnackBar(content=ft.Text("✅ Configurações salvas e Site gerado!")))
            page.update()

        coluna_hospedagem = ft.Column([
            ft.Text("🌐 HOSPEDAGEM AUTOMÁTICA", weight=ft.FontWeight.BOLD, size=18),
            ft.Text("Configure seu token para hospedar sites com um clique", size=13, color="#888"),
            ft.Divider(),
            dropdown_servico_hospedagem,
            txt_token,
            txt_nome_site,
            txt_github_repo,
            ft.Row([
                ft.ElevatedButton("🔗 Testar Conexão", on_click=testar_conexao_click),
                ft.ElevatedButton("💾 Salvar Configuração", on_click=salvar_config_upload_click)
            ], wrap=True),
            ft.Divider(),
            ft.Text("🚀 Ações Rápidas", weight=ft.FontWeight.BOLD, size=14),
            ft.Row([
                ft.ElevatedButton("🌐 Hospedar Site Agora", on_click=hospedar_site_click, icon=ft.Icons.CLOUD_UPLOAD)
            ], wrap=True),
            ft.Divider(),
            ft.Container(content=txt_status_hospedagem, padding=10, bgcolor="#1e1e1e", border_radius=6),
            ft.Text("📌 Como obter seu token:", weight=ft.FontWeight.BOLD, size=13),
            ft.Text("🔵 Netlify: app.netlify.com/user/applications/personal", size=11),
            ft.Text("🟢 GitHub: github.com/settings/tokens (marque 'repo')", size=11),
        ], scroll=ft.ScrollMode.AUTO)

        atualizar_lista()

        banner_admob = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.ADS_CLICK, size=20, color="#4caf50"),
                ft.Text("📢 Anúncio AdMob (placeholder)", size=12, color="#888")
            ], alignment=ft.MainAxisAlignment.CENTER),
            height=50, bgcolor="#1e1e1e",
            border=ft.border.all(1, "#333333"),
            border_radius=4, margin=ft.margin.only(top=10), padding=10
        )

        coluna_cadastro = ft.Column([
            ft.Text("📌 Tipo de Comércio", weight=ft.FontWeight.BOLD, size=16),
            dropdown_nicho,
            ft.Divider(),
            ft.Text("📥 Importação de Estoque", weight=ft.FontWeight.BOLD, size=16),
            ft.ElevatedButton(
                text="Carregar Planilha (Excel / CSV)",
                icon=ft.Icons.UPLOAD_FILE,
                on_click=lambda _: file_picker.pick_files(
                    allow_multiple=False,
                    allowed_extensions=["xlsx", "xls", "csv"]
                )
            ),
            ft.Divider(),
            ft.Text("➕ Cadastrar Novo Item", weight=ft.FontWeight.BOLD, size=16),
            txt_nome, txt_modelo, txt_categoria, txt_preco, txt_desc,
            ft.Text("📷 Imagem do Produto", weight=ft.FontWeight.BOLD, size=14),
            btn_selecionar_imagem,
            txt_imagem_nome,
            txt_destaque,
            ft.Row([
                ft.ElevatedButton(content=ft.Text("Salvar Item"), on_click=salvar_peca),
                btn_abrir_site_local
            ], wrap=True),
            link_exibicao,
            banner_admob,
            ft.Divider(),
            ft.Text("📋 Gerenciar Estoque", weight=ft.FontWeight.BOLD, size=16),
            lista_estoque
        ], scroll=ft.ScrollMode.AUTO)

        coluna_config = ft.Column([
            ft.Text("⚙️ Configurações da Loja", weight=ft.FontWeight.BOLD, size=16),
            txt_nome_loja, txt_cnpj,
            ft.Text("🖼️ Logo da Loja", weight=ft.FontWeight.BOLD, size=14),
            btn_selecionar_logo,
            txt_logo_nome,
            txt_whatsapp, txt_instagram,
            ft.Divider(),
            ft.Text("🖼️ Banners do Carrossel", weight=ft.FontWeight.BOLD, size=14),
            ft.Text("Banner 1", size=12), btn_selecionar_banner1, txt_banner1_nome,
            ft.Text("Banner 2", size=12), btn_selecionar_banner2, txt_banner2_nome,
            ft.Text("Banner 3", size=12), btn_selecionar_banner3, txt_banner3_nome,
            ft.Divider(),
            dropdown_cor, dropdown_tema,
            ft.Container(height=10),
            ft.ElevatedButton(content=ft.Text("💾 Salvar e Gerar Site"), on_click=salvar_config)
        ], scroll=ft.ScrollMode.AUTO)

        def gerar_site_com_oferta(e):
            def continuar_geracao(e):
                page.launch_url(LINK_DIRETO)
                dialog.open = False
                page.update()
                salvar_config(None)
                page.open(ft.SnackBar(content=ft.Text("✅ Site gerado com sucesso!")))
                page.update()

            def pular(e):
                dialog.open = False
                page.update()
                salvar_config(None)
                page.open(ft.SnackBar(content=ft.Text("✅ Site gerado com sucesso!")))
                page.update()

            dialog = ft.AlertDialog(
                title=ft.Text("📢 Apoie o Projeto"),
                content=ft.Column([
                    ft.Text("O SimplyON é gratuito graças ao apoio de parceiros!", size=14),
                    ft.Text("Para gerar seu site, clique no link abaixo e veja ofertas exclusivas.", size=13, color="#888"),
                    ft.Container(height=10),
                    ft.ElevatedButton(
                        "🔥 Ver ofertas e gerar site",
                        on_click=continuar_geracao,
                        bgcolor="#ff5722",
                        color="white",
                        width=300,
                    ),
                    ft.TextButton(
                        "Pular (mas o projeto precisa do seu apoio!)",
                        on_click=pular,
                    ),
                ]),
                actions_alignment=ft.MainAxisAlignment.CENTER,
            )
            page.open(dialog)
            page.update()

        btn_gerar_site = ft.ElevatedButton(
            text="💾 Salvar e Gerar Site",
            on_click=gerar_site_com_oferta,
            icon=ft.Icons.SAVE,
        )
        coluna_config.controls[-1] = btn_gerar_site

        painel_conteudo = ft.Container(content=coluna_cadastro, padding=10)

        def mudar_secao(e):
            if e.control.selected_index == 0:
                painel_conteudo.content = coluna_cadastro
            elif e.control.selected_index == 1:
                painel_conteudo.content = coluna_config
            else:
                painel_conteudo.content = coluna_hospedagem
            page.update()

        page.navigation_bar = ft.NavigationBar(
            selected_index=0,
            on_change=mudar_secao,
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.ADD_BOX, label="Cadastro"),
                ft.NavigationBarDestination(icon=ft.Icons.SETTINGS, label="Configurações"),
                ft.NavigationBarDestination(icon=ft.Icons.CLOUD, label="Hospedagem")
            ]
        )
        page.scroll = ft.ScrollMode.AUTO

        page.controls.clear()
        page.add(painel_conteudo)
        page.update()

    threading.Thread(target=carregar_app_com_splash, daemon=True).start()


if __name__ == "__main__":
    ft.app(target=main)
