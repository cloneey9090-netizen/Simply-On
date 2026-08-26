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

# ============================================================
# ===== SEU ID DO ADSENSE (FIXO) =============================
# ============================================================
MEU_ADSENSE_ID = "pub-1234567890123456"  # SUBSTITUA PELO SEU ID REAL

# ============================================================
# ===== CONFIGURAÇÕES DE PASTAS ==============================
# ============================================================
PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_JSON = os.path.join(PASTA_ATUAL, "estoque.json")
ARQUIVO_CONFIG = os.path.join(PASTA_ATUAL, "config.json")
ARQUIVO_HTML = os.path.join(PASTA_ATUAL, "index.html")
ARQUIVO_UPLOAD_CONFIG = os.path.join(PASTA_ATUAL, "upload_config.json")
PASTA_IMAGENS = os.path.join(PASTA_ATUAL, "imagens")
PASTA_BIN = os.path.join(PASTA_ATUAL, "bin")

if not os.path.exists(PASTA_IMAGENS):
    os.makedirs(PASTA_IMAGENS)
if not os.path.exists(PASTA_BIN):
    os.makedirs(PASTA_BIN)

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

# ============================================================
# ===== FUNÇÃO PARA ENDEREÇO DO SERVIDOR =====================
# ============================================================
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
def iniciar_servidor_web():
    porta = 8550
    diretorio_atual = os.path.dirname(os.path.abspath(__file__))
    os.chdir(diretorio_atual)

    Handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.ThreadingTCPServer(("0.0.0.0", porta), Handler) as httpd:
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
# ===== PIKOTUNNEL ===========================================
# ============================================================
def verificar_pikotunnel_instalado():
    try:
        resultado = subprocess.run(
            ["pm", "list", "packages", "com.pikotunnel"],
            capture_output=True,
            text=True
        )
        return "com.pikotunnel" in resultado.stdout
    except:
        return False

def abrir_pikotunnel(porta=8550):
    if not verificar_pikotunnel_instalado():
        webbrowser.open("https://play.google.com/store/apps/details?id=com.pikotunnel")
        return "❌ PikoTunnel não está instalado. Baixe na Play Store."

    try:
        comando = [
            "am", "start",
            "-n", "com.pikotunnel/.MainActivity",
            "--es", "host", "127.0.0.1",
            "--es", "port", str(porta),
            "--ez", "auto_start", "true"
        ]
        subprocess.run(comando, check=True)
        return f"✅ PikoTunnel iniciado para a porta {porta}."
    except Exception as e:
        return f"❌ Erro ao abrir PikoTunnel: {e}"

def ler_link_pikotunnel():
    try:
        caminhos = [
            "/sdcard/pikotunnel.log",
            "/storage/emulated/0/pikotunnel.log",
            "/data/data/com.pikotunnel/files/log.txt"
        ]
        for caminho in caminhos:
            if os.path.exists(caminho):
                with open(caminho, "r") as f:
                    conteudo = f.read()
                    match = re.search(r'https://[a-zA-Z0-9-]+\.pikotunnel\.com', conteudo)
                    if match:
                        return match.group()
        return None
    except:
        return None

# ============================================================
# ===== TÚNEL CLOUDFLARE (FALLBACK) =========================
# ============================================================
link_publico = ""
tunel_ativo = False
processo_tunel = None

def baixar_cloudflared():
    global PASTA_BIN
    is_windows = sys.platform == "win32"
    is_android = "ANDROID_ROOT" in os.environ or "TERMUX" in os.environ

    if is_windows:
        cloudflared_path = os.path.join(PASTA_BIN, "cloudflared.exe")
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    elif is_android:
        cloudflared_path = os.path.join(PASTA_BIN, "cloudflared")
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
    else:
        cloudflared_path = os.path.join(PASTA_BIN, "cloudflared")
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

    if os.path.exists(cloudflared_path):
        return cloudflared_path

    try:
        print(f"📥 Baixando cloudflared de: {url}")
        urllib.request.urlretrieve(url, cloudflared_path)
        if not is_windows:
            os.chmod(cloudflared_path, 0o755)
        return cloudflared_path
    except Exception as e:
        print(f"❌ Erro ao baixar cloudflared: {e}")
        return None

def baixar_cloudflared_para_local():
    cloudflared_path = baixar_cloudflared()
    if not cloudflared_path:
        return None

    if 'ANDROID_ROOT' in os.environ:
        try:
            destino = "/data/local/tmp/cloudflared"
            shutil.copy2(cloudflared_path, destino)
            os.chmod(destino, 0o755)
            return destino
        except Exception as e:
            print(f"⚠️ Não foi possível copiar para /data/local/tmp: {e}")

    try:
        os.chmod(cloudflared_path, 0o755)
        return cloudflared_path
    except:
        return None

def iniciar_tunel_pinggy(porta=8550):
    try:
        url = "https://pinggy.io/api/tunnels"
        data = {"port": porta}
        response = requests.post(url, json=data, timeout=15)
        if response.status_code == 200:
            result = response.json()
            if "public_url" in result:
                return result["public_url"], None
        return None, "Falha ao criar túnel Pinggy"
    except Exception as e:
        return None, str(e)

def iniciar_tunel_serveo(porta=8550):
    try:
        response = requests.post(
            "https://serveo.net",
            data={"port": porta},
            timeout=20
        )
        if response.status_code == 200:
            match = re.search(r'https://[a-zA-Z0-9-]+\.serveo\.net', response.text)
            if match:
                return match.group(), None
        return None, "Falha ao obter link do Serveo"
    except Exception as e:
        return None, str(e)

def iniciar_tunel_cloudflare():
    global link_publico, tunel_ativo, processo_tunel

    try:
        cloudflared_path = baixar_cloudflared_para_local()
        if cloudflared_path and os.path.exists(cloudflared_path):
            os.chmod(cloudflared_path, 0o755)

            if 'ANDROID_ROOT' in os.environ:
                comando = ["sh", "-c", f"{cloudflared_path} tunnel --url http://127.0.0.1:8550"]
            else:
                comando = [cloudflared_path, "tunnel", "--url", "http://localhost:8550"]

            processo_tunel = subprocess.Popen(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            time.sleep(5)
            for _ in range(30):
                line = processo_tunel.stderr.readline() if processo_tunel.stderr else ""
                if not line:
                    break
                if "trycloudflare.com" in line:
                    match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                    if match:
                        link_publico = match.group()
                        tunel_ativo = True
                        return f"✅ Túnel Cloudflare ativo! Link: {link_publico}"

            time.sleep(10)
            if tunel_ativo and link_publico:
                return f"✅ Túnel Cloudflare ativo! Link: {link_publico}"
    except Exception as e:
        print(f"❌ Erro no cloudflared: {e}")

    link, erro = iniciar_tunel_pinggy()
    if link:
        link_publico = link
        tunel_ativo = True
        return f"✅ Túnel Pinggy ativo! Link: {link}"

    link, erro = iniciar_tunel_serveo()
    if link:
        link_publico = link
        tunel_ativo = True
        return f"✅ Túnel Serveo ativo! Link: {link}"

    ip = obter_ip_local()
    link_publico = f"http://{ip}:8550"
    tunel_ativo = True
    return f"✅ Link local (rede Wi-Fi): {link_publico}"

def parar_tunel():
    global tunel_ativo, processo_tunel, link_publico
    try:
        if processo_tunel:
            processo_tunel.terminate()
            processo_tunel = None
        tunel_ativo = False
        link_publico = ""
    except Exception as e:
        print(f"Erro ao encerrar túnel: {e}")

# ============================================================
# ===== CONFIGURAÇÕES DOS NICHOS =============================
# ============================================================
def obter_config_nicho(nicho_escolhido):
    configs = {
        "🏍️ Peças de Moto Usada": {
            "icone": "🏍️",
            "categorias_padrao": ["Motor", "Suspensão", "Freio", "Transmissão", "Elétrica", "Carroceria"],
            "cores_sugeridas": ["#ff5722", "#000000", "#ff6b35"],
            "banners": ["PEÇAS ORIGINAIS PARA SUA MOTO", "CONFIANÇA E QUALIDADE EM CADA PEÇA",
                        "MELHOR PREÇO DO MERCADO"],
            "descricao_padrao": "Peça original com garantia de fábrica. Pronta entrega.",
            "exemplos": ["Motor C100", "Amortecedor Dianteiro", "Pastilha de Freio", "Corrente de Transmissão"]
        },
        "🐶 PetShop / Animais": {
            "icone": "🐶",
            "categorias_padrao": ["Rações", "Brinquedos", "Banho e Tosa", "Acessórios", "Medicamentos", "Higiene"],
            "cores_sugeridas": ["#4caf50", "#8bc34a", "#ff9800"],
            "banners": ["CUIDADO E CARINHO PARA SEU PET", "OS MELHORES PRODUTOS PARA ANIMAIS",
                        "AMAMOS SEU ANIMAL DE ESTIMAÇÃO"],
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

# ============================================================
# ===== GERAR SITE ===========================================
# ============================================================
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
    adsense_id = MEU_ADSENSE_ID

    if not banners or not banners[0].get("url"):
        banners = [
            {"url": "https://images.unsplash.com/photo-1558981403-c5f9899a28bc", "frase": config_nicho["banners"][0]},
            {"url": "https://images.unsplash.com/photo-1568772585407-9361f9bf3a87", "frase": config_nicho["banners"][1]},
            {"url": "https://images.unsplash.com/photo-1609630875176-b800c92cf03d", "frase": config_nicho["banners"][2]}
        ]

    if tema == "Claro":
        bg_body = "#f4f6f8"
        bg_header = "#ffffff"
        bg_card = "#ffffff"
        text_main = "#222222"
        text_muted = "#666666"
        border_color = "#e0e0e0"
        input_bg = "#ffffff"
    else:
        bg_body = "#121212"
        bg_header = "#1a1a1a"
        bg_card = "#1a1a1a"
        text_main = "#f1f1f1"
        text_muted = "#aaaaaa"
        border_color = "#333333"
        input_bg = "#121212"

    carousel_html = ""
    for i, banner in enumerate(banners):
        url = banner.get("url", "")
        active = "active" if i == 0 else ""
        carousel_html += f'<div class="carousel-slide {active}" style="background-image: url(\'{url}\');"></div>'

    adsense_html = ""
    if adsense_id:
        adsense_html = f"""
        <div style="max-width:1100px; margin:20px auto; padding:0 15px;">
            <ins class="adsbygoogle"
                 style="display:block"
                 data-ad-client="{adsense_id}"
                 data-ad-slot="1234567890"
                 data-ad-format="auto"
                 data-full-width-responsive="true"></ins>
            <script>
                 (adsbygoogle = window.adsbygoogle || []).push{{}});
            </script>
        </div>
        """

    html_conteudo = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{nova_config.get('nome_loja', 'Loja')}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    {f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={adsense_id}" crossorigin="anonymous"></script>' if adsense_id else ''}
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

        .carousel-container {{
            position: relative;
            width: 100%;
            height: auto;
            aspect-ratio: 16 / 9;
            overflow: hidden;
        }}
        .carousel-slide {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-size: cover;
            background-position: center;
            opacity: 0;
            transition: opacity 1.2s ease-in-out;
        }}
        .carousel-slide.active {{
            opacity: 1;
        }}
        .carousel-slide::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.15);
            z-index: 1;
        }}

        .linha-destaque {{ height: 3px; background-color: {cor_hex}; width: 100%; }}
        .container {{ max-width: 1100px; margin: 30px auto; padding: 0 15px; min-height: 400px; }}
        h2 {{ font-size: 20px; text-transform: uppercase; letter-spacing: 1px; border-left: 4px solid {cor_hex}; padding-left: 10px; color: {text_main}; margin-bottom: 20px; }}

        .filtros-container {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; padding: 10px 0; border-bottom: 1px solid {border_color}; }}
        .filtro-btn {{ padding: 6px 14px; border: 2px solid {border_color}; border-radius: 25px; background: transparent; color: {text_muted}; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.3s ease; text-transform: capitalize; }}
        .filtro-btn:hover, .filtro-btn.ativo {{ background: {cor_hex}; color: #fff; border-color: {cor_hex}; }}
        .filtro-btn .contagem {{ display: inline-block; background: rgba(255,255,255,0.2); border-radius: 12px; padding: 0 8px; font-size: 11px; margin-left: 5px; }}
        .filtro-btn.ativo .contagem {{ background: rgba(255,255,255,0.3); }}

        .grid-produtos {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 15px;
        }}
        .card {{
            background: {bg_card};
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            border: 1px solid {border_color};
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: transform 0.2s;
            position: relative;
        }}
        .card:hover {{ transform: translateY(-4px); }}
        .card img {{
            width: 100%;
            height: 150px;
            object-fit: cover;
        }}
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

        @media (max-width: 600px) {{
            header {{ padding: 10px 15px; }}
            .logo {{ max-height: 50px !important; }}
            .search-header {{ max-width: 160px; min-width: 100px; }}
            .btn-carrinho-topo {{ padding: 5px 10px; font-size: 11px; }}
            .filtros-container {{ gap: 5px; }}
            .filtro-btn {{ padding: 5px 10px; font-size: 11px; }}
            .grid-produtos {{ grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }}
            .card img {{ height: 120px; }}
            .carousel-container {{ aspect-ratio: 16 / 9; }}
            .card-title {{ font-size: 14px; }}
            .preco {{ font-size: 14px; }}
        }}
        @media (max-width: 400px) {{
            .grid-produtos {{ grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); }}
            .card img {{ height: 100px; }}
        }}
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
    {adsense_html}
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
</html>
"""
    with open(ARQUIVO_HTML, "w", encoding="utf-8") as f:
        f.write(html_conteudo)

    disparar_servidor_em_segundo_plano()

# ============================================================
# ===== FUNÇÃO PRINCIPAL =====================================
# ============================================================
def main(page: ft.Page):
    global link_publico, tunel_ativo

    page.title = "Painel do Comandante"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 480
    page.window.height = 720

    # ===== INTERCEPTAR TECLA "VOLTAR" =====
    def on_keyboard(e: ft.KeyboardEvent):
        if e.key == "Back":
            page.open(ft.SnackBar(content=ft.Text("🔄 Use o botão Home ou minimize para manter o site ativo.")))
            return True

    page.on_keyboard_event = on_keyboard

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

    # ===== CAMPOS DO FORMULÁRIO =====
    txt_nome = ft.TextField(label="Nome da Peça")
    txt_modelo = ft.TextField(label="Modelo")
    txt_categoria = ft.TextField(label="Categoria")
    txt_preco = ft.TextField(label="Preço (Ex: R$ 150,00)")
    txt_desc = ft.TextField(label="Descrição")

    txt_destaque = ft.Dropdown(
        label="Produto Destaque?",
        value="Não",
        options=[
            ft.dropdown.Option("Não"),
            ft.dropdown.Option("Sim")
        ]
    )

    # ===== CAMPOS DE IMAGEM DO PRODUTO =====
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

    # ===== LOGO =====
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

    # ===== BANNERS =====
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
        "🏍️ Peças de Moto Usada",
        "🐶 PetShop / Animais",
        "🚲 Bicicletas / Bike",
        "📱 Eletrônicos / Celulares",
        "👗 Moda / Roupas",
        "🛋️ Móveis / Decoração",
        "🍔 Alimentação / Mercado",
        "🛍️ Loja de Variedades",
        "💄 Beleza e Estética",
        "🏋️ Academia e Esportes",
        "📚 Livros e Papelaria",
        "🎸 Instrumentos Musicais",
        "🧸 Brinquedos e Infantil",
        "🌿 Jardinagem e Paisagismo",
        "🔧 Ferramentas e Construção",
        "🎮 Games e Informática",
        "🚗 Automóveis e Peças"
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
                nome_arquivo = f"produto_{item_novo['id']}{extensao}"
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
                    "id": proximo_id,
                    "nome": nome_val,
                    "modelo": str(row[col_modelo]) if col_modelo and pd.notna(row[col_modelo]) else "Padrão",
                    "categoria": str(row[col_categoria]) if col_categoria and pd.notna(row[col_categoria]) else "Geral",
                    "status": "Disponível",
                    "preco": str(row[col_preco]) if pd.notna(row[col_preco]) else "R$ 0,00",
                    "descricao": str(row[col_desc]) if col_desc and pd.notna(row[col_desc]) else "",
                    "imagem": imagem_url,
                    "destaque": False
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

    # ============================================================
    # ===== FUNÇÕES DE HOSPEDAGEM (VIA API REST) =================
    # ============================================================
    def carregar_config_upload():
        padrao = {
            "servico": "Netlify",
            "token": "",
            "site_name": "",
            "github_repo": ""
        }
        if not os.path.exists(ARQUIVO_UPLOAD_CONFIG):
            with open(ARQUIVO_UPLOAD_CONFIG, "w", encoding="utf-8") as f:
                json.dump(padrao, f, indent=2)
            return padrao
        with open(ARQUIVO_UPLOAD_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)

    def salvar_config_upload(token, site_name, servico, github_repo):
        config = {
            "servico": servico,
            "token": token,
            "site_name": site_name,
            "github_repo": github_repo
        }
        with open(ARQUIVO_UPLOAD_CONFIG, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True

    def testar_conexao_netlify(token):
        try:
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(
                "https://api.netlify.com/api/v1/sites",
                headers=headers,
                timeout=10
            )
            return response.status_code == 200
        except:
            return False

    def testar_conexao_github(token):
        try:
            headers = {"Authorization": f"token {token}"}
            response = requests.get(
                "https://api.github.com/user",
                headers=headers,
                timeout=10
            )
            return response.status_code == 200
        except:
            return False

    def hospedar_netlify(pasta_do_site, token, site_name):
        try:
            if not token:
                return None, "Token não configurado"

            headers = {"Authorization": f"Bearer {token}"}
            url_sites = "https://api.netlify.com/api/v1/sites"

            # 1. Listar sites para ver se já existe
            response = requests.get(url_sites, headers=headers)
            if response.status_code != 200:
                return None, f"Erro ao listar sites: {response.status_code}"

            sites = response.json()
            site_id = None

            for site in sites:
                if site.get("name") == site_name:
                    site_id = site["id"]
                    break

            # 2. Se não encontrou, cria um novo site
            if not site_id:
                create_data = {"name": site_name}
                response = requests.post(url_sites, headers=headers, json=create_data)

                if response.status_code in [200, 201]:
                    site_id = response.json()["id"]
                else:
                    return None, f"Erro ao criar site (Tente outro nome): {response.status_code} - {response.text}"

            if not site_id:
                return None, "Não foi possível obter o site_id."

            # 3. Lê o index.html
            index_path = os.path.join(pasta_do_site, "index.html")
            if not os.path.exists(index_path):
                return None, "Arquivo index.html não encontrado"

            # 4. Compacta o index.html + pasta imagens/ em um arquivo ZIP
            zip_path = os.path.join(pasta_do_site, "deploy.zip")

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Adiciona o index.html na raiz
                zipf.write(index_path, arcname="index.html")

                # Adiciona a pasta imagens/ se existir
                imagens_path = os.path.join(pasta_do_site, "imagens")
                if os.path.exists(imagens_path):
                    for root, dirs, files in os.walk(imagens_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, pasta_do_site)
                            zipf.write(file_path, arcname=arcname)

            # 5. Faz o deploy enviando o ZIP com o Content-Type correto
            deploy_url = f"https://api.netlify.com/api/v1/sites/{site_id}/deploys"

            with open(zip_path, "rb") as zip_file:
                zip_data = zip_file.read()

            # HEADER CORRETO PARA O NETLIFY RECONHECER COMO HTML
            headers_deploy = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/zip"
            }
            response = requests.post(deploy_url, headers=headers_deploy, data=zip_data)

            # Remove o zip temporário
            if os.path.exists(zip_path):
                os.remove(zip_path)

            if response.status_code not in [200, 201, 202]:
                return None, f"Erro no deploy: {response.status_code} - {response.text}"

            # 6. Pega a URL oficial
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
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            url_check = f"https://api.github.com/repos/{repo_nome}/contents/index.html"
            response_check = requests.get(url_check, headers=headers)
            if response_check.status_code == 200:
                sha = response_check.json()["sha"]
                data = {
                    "message": "Site atualizado automaticamente",
                    "content": conteudo_base64,
                    "sha": sha,
                    "branch": "main"
                }
                response = requests.put(url_check, headers=headers, json=data)
            else:
                data = {
                    "message": "Site criado automaticamente",
                    "content": conteudo_base64,
                    "branch": "main"
                }
                response = requests.put(url_check, headers=headers, json=data)
            if response.status_code in [200, 201]:
                url = f"https://{repo_nome.split('/')[0]}.github.io/{repo_nome.split('/')[1]}"
                return url, None
            else:
                return None, f"Erro ao enviar para GitHub: {response.status_code}"
        except Exception as e:
            return None, f"Erro ao hospedar: {str(e)}"

    # ============================================================
    # ===== CONFIGURAÇÕES ========================================
    # ============================================================
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

    # ============================================================
    # ===== PAINEL DE HOSPEDAGEM =================================
    # ============================================================
    txt_token = ft.TextField(
        label="🔑 Token de Acesso",
        hint_text="Cole aqui o token gerado no Netlify ou GitHub",
        password=True,
        width=400
    )

    txt_nome_site = ft.TextField(
        label="📝 Nome do Site (Netlify)",
        hint_text="Ex: vitrine",
        width=400
    )

    txt_github_repo = ft.TextField(
        label="📂 Repositório GitHub",
        hint_text="Ex: usuario/repositorio",
        width=400,
        visible=False
    )

    dropdown_servico_hospedagem = ft.Dropdown(
        label="🌐 Serviço de Hospedagem",
        value="Netlify",
        options=[
            ft.dropdown.Option("Netlify"),
            ft.dropdown.Option("GitHub"),
        ],
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

    txt_status_hospedagem = ft.Text(
        "⚪ Aguardando configuração...",
        size=12,
        color="#888"
    )

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
            url, erro = hospedar_netlify(PASTA_ATUAL, token, site_name)
        else:
            repo = config_upload.get("github_repo", "")
            if not repo:
                txt_status_hospedagem.value = "❌ Repositório GitHub não configurado!"
                txt_status_hospedagem.color = "#ff5722"
                page.update()
                return
            url, erro = hospedar_github(PASTA_ATUAL, token, repo)

        if url:
            txt_status_hospedagem.value = f"✅ Site hospedado: {url}"
            txt_status_hospedagem.color = "#4caf50"
            mostrar_link(url)  # MOSTRA O LINK NO CARTÃO
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

    # ============================================================
    # ===== LINK PÚBLICO (EXIBIÇÃO PERSISTENTE) ==================
    # ============================================================
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
                ),
            ]),
        ]),
        padding=10,
        bgcolor="#1e1e1e",
        border_radius=6,
        margin=ft.margin.only(top=10),
        visible=False
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

    # ============================================================
    # ===== BOTÃO ABRIR SITE LOCAL ===============================
    # ============================================================
    def abrir_site_local_click(e):
        global link_publico, tunel_ativo

        if not os.path.exists(ARQUIVO_HTML):
            page.open(ft.SnackBar(content=ft.Text("❌ Gere o site primeiro!")))
            page.update()
            return

        disparar_servidor_em_segundo_plano()

        if 'ANDROID_ROOT' in os.environ:
            mensagem = abrir_pikotunnel(8550)
            page.open(ft.SnackBar(content=ft.Text(mensagem)))
            link = ler_link_pikotunnel()
            if link:
                link_publico = link
                mostrar_link(link_publico)
            else:
                link_publico = "📱 Verifique a notificação do PikoTunnel"
                mostrar_link(link_publico)
        else:
            if not tunel_ativo:
                mensagem = iniciar_tunel_cloudflare()
                page.open(ft.SnackBar(content=ft.Text(mensagem)))
            if tunel_ativo and link_publico:
                mostrar_link(link_publico)

        webbrowser.open(f"http://{obter_endereco_servidor()}:8550")
        page.update()

    btn_abrir_site_local = ft.ElevatedButton(
        "📤 Compartilhar Meu Catálogo",
        on_click=abrir_site_local_click,
        icon=ft.Icons.SHARE,
        width=200,
    )

    # ============================================================
    # ===== SALVAR CONFIGURAÇÕES =================================
    # ============================================================
    def salvar_config(e):
        nonlocal config, caminho_logo_selecionada, caminho_banner1_selecionado, caminho_banner2_selecionado, caminho_banner3_selecionado

        cor_selecionada = dropdown_cor.value

        logo_final = config.get("logo_url", "")
        if caminho_logo_selecionada and os.path.exists(caminho_logo_selecionada):
            try:
                if not os.path.exists(PASTA_IMAGENS):
                    os.makedirs(PASTA_IMAGENS)
                extensao = os.path.splitext(caminho_logo_selecionada)[1]
                destino = os.path.join(PASTA_IMAGENS, f"logo{extensao}")
                shutil.copy2(caminho_logo_selecionada, destino)
                logo_final = f"imagens/logo{extensao}"
                caminho_logo_selecionada = ""
                txt_logo_nome.value = "📷 Nenhuma logo selecionada"
            except Exception as ex:
                print(f"Erro ao copiar logo: {ex}")

        banners = []

        banner1_final = ""
        if caminho_banner1_selecionado and os.path.exists(caminho_banner1_selecionado):
            try:
                extensao = os.path.splitext(caminho_banner1_selecionado)[1]
                destino = os.path.join(PASTA_IMAGENS, f"banner1{extensao}")
                shutil.copy2(caminho_banner1_selecionado, destino)
                banner1_final = f"imagens/banner1{extensao}"
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
                destino = os.path.join(PASTA_IMAGENS, f"banner2{extensao}")
                shutil.copy2(caminho_banner2_selecionado, destino)
                banner2_final = f"imagens/banner2{extensao}"
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
                destino = os.path.join(PASTA_IMAGENS, f"banner3{extensao}")
                shutil.copy2(caminho_banner3_selecionado, destino)
                banner3_final = f"imagens/banner3{extensao}"
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

    # ============================================================
    # ===== COLUNAS ==============================================
    # ============================================================
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
            ft.ElevatedButton("💾 Salvar Configuração", on_click=salvar_config_upload_click),
        ], wrap=True),
        ft.Divider(),
        ft.Text("🚀 Ações Rápidas", weight=ft.FontWeight.BOLD, size=14),
        ft.Row([
            ft.ElevatedButton("🌐 Hospedar Site Agora", on_click=hospedar_site_click, icon=ft.Icons.CLOUD_UPLOAD),
        ], wrap=True),
        ft.Divider(),
        ft.Container(
            content=txt_status_hospedagem,
            padding=10,
            bgcolor="#1e1e1e",
            border_radius=6,
        ),
        ft.Text("📌 Como obter seu token:", weight=ft.FontWeight.BOLD, size=13),
        ft.Text("🔵 Netlify: app.netlify.com/user/applications/personal", size=11),
        ft.Text("🟢 GitHub: github.com/settings/tokens (marque 'repo')", size=11),
    ], scroll=ft.ScrollMode.AUTO)

    atualizar_lista()

    # ============================================================
    # ===== ANÚNCIO ADMOB (BANNER) ===============================
    # ============================================================
    banner_admob = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.ADS_CLICK, size=20, color="#4caf50"),
            ft.Text("📢 Anúncio AdMob (placeholder)", size=12, color="#888"),
        ], alignment=ft.MainAxisAlignment.CENTER),
        height=50,
        bgcolor="#1e1e1e",
        border=ft.border.all(1, "#333333"),
        border_radius=4,
        margin=ft.margin.only(top=10),
        padding=10,
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
            btn_abrir_site_local,
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
        ft.Text("Banner 1", size=12),
        btn_selecionar_banner1,
        txt_banner1_nome,
        ft.Text("Banner 2", size=12),
        btn_selecionar_banner2,
        txt_banner2_nome,
        ft.Text("Banner 3", size=12),
        btn_selecionar_banner3,
        txt_banner3_nome,
        ft.Divider(),
        dropdown_cor, dropdown_tema,
        ft.Container(height=10),
        ft.ElevatedButton(content=ft.Text("💾 Salvar e Gerar Site"), on_click=salvar_config)
    ], scroll=ft.ScrollMode.AUTO)

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
            ft.NavigationBarDestination(icon=ft.Icons.CLOUD, label="Hospedagem"),
        ]
    )

    page.scroll = ft.ScrollMode.AUTO
    page.add(painel_conteudo)

if __name__ == "__main__":
    ft.app(target=main)
