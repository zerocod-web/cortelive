// CorteLive.cs — Lançador único e servidor local do Painel CorteLive (Windows 10/11)
// Compilado com o csc.exe nativo do .NET Framework 4.x presente em todo Windows 10 e 11.
// Sem privilégios de administrador (asInvoker), sem dependências externas.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

#if MODO_DEV
[assembly: AssemblyTitle("CorteLive Dev")]
[assembly: AssemblyDescription("Painel de desenvolvedor do CorteLive (MODO_DEV)")]
[assembly: AssemblyProduct("CorteLive Dev")]
#else
[assembly: AssemblyTitle("CorteLive")]
[assembly: AssemblyDescription("Painel de cortes automáticos de live")]
[assembly: AssemblyProduct("CorteLive")]
#endif

namespace CorteLivePainel
{
    public static class Program
    {
#if MODO_DEV
        private const string NomeApp = "CorteLive [DEV]";
        private const string ArqInstanciaNome = "painel_instancia_dev.json";
        private const string IconeApp = "cortelive-dev.ico";
        private const string CanalAtualizacao = "beta";
#else
        private const string NomeApp = "CorteLive";
        private const string ArqInstanciaNome = "painel_instancia.json";
        private const string IconeApp = "cortelive.ico";
        private const string CanalAtualizacao = "estavel";
#endif

        private static string Raiz;
        private static int Porta = 0;
        private static bool Headless = false;
        private static bool SelfTest = false;
        private static string AbaInicial = "";
        private static string JanelaPos = "";
        private static string JanelaTam = "1160,840";
        private static volatile bool Encerrando = false;
        private static TcpListener Listener;
        private static NotifyIcon TrayIcon;

        private static DateTime UltimoHeartbeat = DateTime.UtcNow;
        private static bool JanelaAbertaAlgumaVez = false;
        private static DateTime? JanelaFechadaEm = null;
        private static Process ProcJanela = null;

        // Atualização em segundo plano: o painel acompanha o progresso por /api/estado.
        private static readonly object LockAtual = new object();
        private static volatile bool AtualRodando = false;
        private static Dictionary<string, object> AtualUltimo = null;
        // Reabertura depois da atualização (processo auxiliar que fecha o app antigo e abre o novo).
        private static bool ModoReabrir = false;
        private static int ReabrirPortaAntiga = 0;
        private static int ReabrirPidAntigo = 0;
        private static string ReabrirVersao = "";
        private static string AtualizadoPara = "";

        // Estado de instalação
        private static readonly object LockInst = new object();
        private static Process ProcInstalacao = null;
        private static bool InstRodando = false;
        private static int InstEtapa = 0;
        private static double InstPct = 0.0;
        private static string InstTituloEtapa = "";
        private static string InstDetalhe = "";
        private static string InstErro = null;
        private static string InstOQueFazer = null;
        private static readonly List<string> InstLinhasLog = new List<string>();
        private static bool? CacheImportsOk = null;

        // Estado de sessão de corte
        private static readonly object LockSessao = new object();
        private static Process ProcSessao = null;
        private static bool SessaoRodando = false;
        private static string SessaoEstado = "parado";
        private static string SessaoCanal = "";
        private static double SessaoLiveS = 0.0;
        private static double? SessaoAtrasoS = null;
        private static string SessaoAtrasoCanal = null;
        private static double? SessaoUltimoCorteHaS = null;
        private static int SessaoCortes = 0;
        private static string SessaoUltimaMsg = "";
        private static string SessaoDetalheServicos = "";
        private static string SessaoErro = null;
        private static readonly List<string> SessaoLinhasLog = new List<string>();
        private static readonly List<Dictionary<string, object>> SessaoLives = new List<Dictionary<string, object>>();

        [STAThread]
        public static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Raiz = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
#if MODO_DEV
            bool raizExplicita = false;
#endif
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (string.Equals(a, "--headless", StringComparison.OrdinalIgnoreCase))
                {
                    Headless = true;
                }
                else if (string.Equals(a, "--self-test", StringComparison.OrdinalIgnoreCase))
                {
                    SelfTest = true;
                }
                else if (string.Equals(a, "--raiz", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    Raiz = Path.GetFullPath(args[++i]).TrimEnd('\\', '/');
#if MODO_DEV
                    raizExplicita = true;
#endif
                }
                else if (string.Equals(a, "--porta", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    int p;
                    if (int.TryParse(args[++i], out p)) Porta = p;
                }
                else if (string.Equals(a, "--aba", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    AbaInicial = args[++i].Trim();
                }
                else if (string.Equals(a, "--janela-pos", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    JanelaPos = args[++i].Trim();
                }
                else if (string.Equals(a, "--janela-tam", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    JanelaTam = args[++i].Trim();
                }
                else if (string.Equals(a, "--reabrir-apos-atualizar", StringComparison.OrdinalIgnoreCase))
                {
                    ModoReabrir = true;
                }
                else if (string.Equals(a, "--porta-antiga", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    int.TryParse(args[++i], out ReabrirPortaAntiga);
                }
                else if (string.Equals(a, "--pid-antigo", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    int.TryParse(args[++i], out ReabrirPidAntigo);
                }
                else if (string.Equals(a, "--versao-nova", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    ReabrirVersao = args[++i].Trim();
                }
                else if (string.Equals(a, "--atualizado", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                {
                    AtualizadoPara = args[++i].Trim();
                }
            }

#if MODO_DEV
            // Roda direto na pasta do projeto (mesmo quando iniciado a partir de dist\CorteLive Dev.exe)
            if (!raizExplicita && !File.Exists(Path.Combine(Raiz, @"painel\index.html")))
            {
                DirectoryInfo pai = Directory.GetParent(Raiz);
                if (pai != null &&
                    File.Exists(Path.Combine(pai.FullName, @"painel\index.html")) &&
                    File.Exists(Path.Combine(pai.FullName, @"cortelive\__init__.py")))
                {
                    Raiz = pai.FullName.TrimEnd('\\', '/');
                }
            }
#endif

            try
            {
                Directory.CreateDirectory(Raiz);
#if !MODO_DEV
                ExtrairPacoteEmbutidoSeNecessario();
#endif
            }
            catch (Exception ex)
            {
                if (SelfTest || Headless)
                {
                    Console.Error.WriteLine("ERRO_STARTUP: " + ex.Message);
                    return 1;
                }
                MessageBox.Show("Erro ao preparar a pasta do CorteLive:\n" + ex.Message,
                    NomeApp, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }

            if (ModoReabrir)
            {
                return ExecutarReabertura();
            }

            if (SelfTest)
            {
                int rTeste = ExecutarSelfTest();
#if !MODO_DEV
                // O atualizador roda o --self-test do .exe NOVO antes de concluir. Se o app aberto
                // é de outra versão, deixa um auxiliar que fecha o app antigo e reabre o novo.
                if (rTeste == 0) AgendarReaberturaSeAtualizando();
#endif
                return rTeste;
            }

            int portaExistente = VerificarInstanciaAtiva();
            if (portaExistente > 0)
            {
                if (!Headless)
                {
                    AbrirJanelaPainel(portaExistente);
                }
                return 0;
            }

            try
            {
                Listener = new TcpListener(IPAddress.Loopback, Porta);
                Listener.Start();
                Porta = ((IPEndPoint)Listener.LocalEndpoint).Port;
            }
            catch (Exception ex)
            {
                if (Headless)
                {
                    Console.Error.WriteLine("ERRO_PORTA: " + ex.Message);
                    return 1;
                }
                MessageBox.Show("Não foi possível iniciar o servidor local do painel:\n" + ex.Message,
                    "CorteLive", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }

            SalvarInstanciaAtiva();

            Thread thHttp = new Thread(LoopServidorHttp);
            thHttp.IsBackground = true;
            thHttp.Start();

            Thread thProgresso = new Thread(LoopMonitorProgressoInstalacao);
            thProgresso.IsBackground = true;
            thProgresso.Start();

            if (Headless)
            {
                Console.WriteLine(String.Format("PAINEL_PRONTO http://127.0.0.1:{0}", Porta));
                Console.Out.Flush();
                while (!Encerrando)
                {
                    Thread.Sleep(250);
                    if (DeveEncerrarPorJanelaFechada())
                    {
                        Encerrando = true;
                        break;
                    }
                }
                LimparAoSair();
                return 0;
            }

            IniciarIconeBandeja();
            AbrirJanelaPainel(Porta);

            System.Windows.Forms.Timer timerVida = new System.Windows.Forms.Timer();
            timerVida.Interval = 1000;
            timerVida.Tick += delegate
            {
                if (Encerrando)
                {
                    timerVida.Stop();
                    Application.ExitThread();
                    return;
                }
                if (DeveEncerrarPorJanelaFechada())
                {
                    Encerrando = true;
                    timerVida.Stop();
                    Application.ExitThread();
                }
            };
            timerVida.Start();

            Application.Run();
            LimparAoSair();
            return 0;
        }

        private static int ExecutarSelfTest()
        {
            string html = Path.Combine(Raiz, @"painel\index.html");
            string js = Path.Combine(Raiz, @"painel\app.js");
            string css = Path.Combine(Raiz, @"painel\estilos.css");
            if (!File.Exists(html) || !File.Exists(js) || !File.Exists(css))
            {
                Console.Error.WriteLine("SELF_TEST_FALHOU: arquivos do painel ausentes.");
                return 1;
            }
#if MODO_DEV
            string devHtml = Path.Combine(Raiz, @"painel\dev\dev.html");
            string devJs = Path.Combine(Raiz, @"painel\dev\dev.js");
            string devCss = Path.Combine(Raiz, @"painel\dev\dev.css");
            if (!File.Exists(devHtml) || !File.Exists(devJs) || !File.Exists(devCss))
            {
                Console.Error.WriteLine("SELF_TEST_FALHOU: arquivos de painel\\dev\\ ausentes.");
                return 1;
            }
#endif
            TcpListener teste = new TcpListener(IPAddress.Loopback, 0);
            teste.Start();
            int p = ((IPEndPoint)teste.LocalEndpoint).Port;
            teste.Stop();
#if MODO_DEV
            Console.WriteLine(String.Format("SELF_TEST_OK modo=dev v{0} porta={1}", LerVersao(), p));
#else
            Console.WriteLine(String.Format("SELF_TEST_OK modo=estavel v{0} porta={1}", LerVersao(), p));
#endif
            return 0;
        }

        private static void ExtrairPacoteEmbutidoSeNecessario()
        {
            string html = Path.Combine(Raiz, @"painel\index.html");
            string inst = Path.Combine(Raiz, @"instalador\instalar.ps1");
            string initPy = Path.Combine(Raiz, @"cortelive\__init__.py");
            if (File.Exists(html) && File.Exists(inst) && File.Exists(initPy))
            {
                return;
            }

            Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("app.zip");
            if (s == null) return;
            using (s)
            using (ZipArchive zip = new ZipArchive(s, ZipArchiveMode.Read))
            {
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    if (string.IsNullOrEmpty(entry.Name)) continue;
                    string destino = Path.GetFullPath(Path.Combine(Raiz, entry.FullName));
                    if (!destino.StartsWith(Raiz + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) continue;
                    string dirPai = Path.GetDirectoryName(destino);
                    if (!string.IsNullOrEmpty(dirPai)) Directory.CreateDirectory(dirPai);
                    if (!File.Exists(destino))
                    {
                        entry.ExtractToFile(destino, false);
                    }
                }
            }
        }

        private static int VerificarInstanciaAtiva()
        {
            string arq = Path.Combine(Raiz, ArqInstanciaNome);
            if (!File.Exists(arq)) return 0;
            try
            {
                JavaScriptSerializer js = new JavaScriptSerializer();
                Dictionary<string, object> d = js.Deserialize<Dictionary<string, object>>(File.ReadAllText(arq, Encoding.UTF8));
                if (d == null || !d.ContainsKey("porta")) return 0;
                int p = Convert.ToInt32(d["porta"]);
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(String.Format("http://127.0.0.1:{0}/api/ping", p));
                req.Timeout = 800;
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                {
                    if (resp.StatusCode == HttpStatusCode.OK) return p;
                }
            }
            catch { }
            return 0;
        }

        private static void SalvarInstanciaAtiva()
        {
            try
            {
                string arq = Path.Combine(Raiz, ArqInstanciaNome);
                string json = String.Format("{{\"porta\":{0},\"pid\":{1}}}", Porta, Process.GetCurrentProcess().Id);
                File.WriteAllText(arq, json, Encoding.UTF8);
            }
            catch { }
        }

        private static bool DeveEncerrarPorJanelaFechada()
        {
            if (InstRodando || AtualRodando) return false;
            double desdeHb = (DateTime.UtcNow - UltimoHeartbeat).TotalSeconds;
            if (JanelaFechadaEm.HasValue)
            {
                double seg = (DateTime.UtcNow - JanelaFechadaEm.Value).TotalSeconds;
                if (seg >= 2.0 && desdeHb >= 1.8) return true;
            }
            if (!Headless && JanelaAbertaAlgumaVez && ProcJanela != null)
            {
                try
                {
                    if (ProcJanela.HasExited && desdeHb >= 4.0) return true;
                }
                catch { }
            }
            return false;
        }

        private static void LimparAoSair()
        {
            Encerrando = true;
            try { if (Listener != null) Listener.Stop(); } catch { }
            try
            {
                if (TrayIcon != null)
                {
                    TrayIcon.Visible = false;
                    TrayIcon.Dispose();
                }
            }
            catch { }
            PararSessaoInterno(true);
            try
            {
                string arq = Path.Combine(Raiz, ArqInstanciaNome);
                if (File.Exists(arq)) File.Delete(arq);
            }
            catch { }
        }

        private static Icon CriarIconeBroadcast()
        {
            try
            {
                Icon assoc = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
                if (assoc != null) return assoc;
            }
            catch { }

            Bitmap bmp = new Bitmap(32, 32);
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.Clear(Color.FromArgb(22, 25, 32));
                using (Pen pen = new Pen(Color.FromArgb(245, 165, 36), 2.5f))
                {
                    g.DrawRectangle(pen, 8, 3, 16, 26);
                }
                using (Brush red = new SolidBrush(Color.FromArgb(239, 68, 68)))
                {
                    g.FillEllipse(red, 13, 8, 6, 6);
                }
                using (Pen white = new Pen(Color.FromArgb(241, 245, 249), 2f))
                {
                    g.DrawLine(white, 11, 20, 21, 20);
                    g.DrawLine(white, 13, 24, 19, 24);
                }
            }
            return Icon.FromHandle(bmp.GetHicon());
        }

        private static void IniciarIconeBandeja()
        {
            try
            {
                ContextMenu menu = new ContextMenu();
                menu.MenuItems.Add("Abrir " + NomeApp, delegate { AbrirJanelaPainel(Porta); });
                menu.MenuItems.Add("Abrir pasta de cortes", delegate { AbrirPastaCortes(""); });
                menu.MenuItems.Add("-");
                menu.MenuItems.Add("Sair de " + NomeApp, delegate
                {
                    Encerrando = true;
                    Application.ExitThread();
                });

                TrayIcon = new NotifyIcon();
                string icone = Path.Combine(Raiz, "painel", "img", IconeApp);
                TrayIcon.Icon = File.Exists(icone) ? new Icon(icone) : CriarIconeBroadcast();
#if MODO_DEV
                TrayIcon.Text = NomeApp;
#else
                TrayIcon.Text = "CorteLive — Painel de Cortes";
#endif
                TrayIcon.ContextMenu = menu;
                TrayIcon.Visible = true;
                TrayIcon.DoubleClick += delegate { AbrirJanelaPainel(Porta); };
            }
            catch { }
        }

        private static string AcharNavegadorApp()
        {
            string[] candidatos = new string[]
            {
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), @"Google\Chrome\Application\chrome.exe")
            };
            foreach (string c in candidatos)
            {
                if (!string.IsNullOrEmpty(c) && File.Exists(c)) return c;
            }
            try
            {
                using (RegistryKey k = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"))
                {
                    if (k != null)
                    {
                        string v = k.GetValue("") as string;
                        if (!string.IsNullOrEmpty(v) && File.Exists(v)) return v;
                    }
                }
            }
            catch { }
            return null;
        }

        private static void AbrirJanelaPainel(int portaAlvo)
        {
#if MODO_DEV
            string sufixoAba = !string.IsNullOrEmpty(AbaInicial) ? ("?aba=" + Uri.EscapeDataString(AbaInicial)) : "";
            string url = String.Format("http://127.0.0.1:{0}/{1}", portaAlvo, sufixoAba);
            string subPerfil = "webview_dev";
#else
            string url = String.Format("http://127.0.0.1:{0}/", portaAlvo);
            if (!string.IsNullOrEmpty(AtualizadoPara))
            {
                url += "?atualizado=" + Uri.EscapeDataString(AtualizadoPara);
                AtualizadoPara = "";
            }
            string subPerfil = "webview_app";
#endif
            JanelaFechadaEm = null;
            UltimoHeartbeat = DateTime.UtcNow;
            try
            {
                string nav = AcharNavegadorApp();
                if (nav != null)
                {
                    string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                    string dirPerfil = Path.Combine(Path.Combine(localApp, "CorteLive"), subPerfil);
                    Directory.CreateDirectory(dirPerfil);

                    StringBuilder sbArgs = new StringBuilder();
                    sbArgs.AppendFormat("--user-data-dir=\"{0}\" --no-first-run --no-default-browser-check --app={1}", dirPerfil, url);
                    if (!string.IsNullOrEmpty(JanelaTam)) sbArgs.AppendFormat(" --window-size={0}", JanelaTam);
                    if (!string.IsNullOrEmpty(JanelaPos)) sbArgs.AppendFormat(" --window-position={0}", JanelaPos);

                    ProcessStartInfo psi = new ProcessStartInfo();
                    psi.FileName = nav;
                    psi.Arguments = sbArgs.ToString();
                    psi.UseShellExecute = false;
                    ProcJanela = Process.Start(psi);
                    return;
                }
            }
            catch { }
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch { }
        }

        // =========================================================================
        // Servidor HTTP Local (127.0.0.1 apenas — zero prompt de Firewall / zero UAC)
        // =========================================================================
        private static void LoopServidorHttp()
        {
            while (!Encerrando)
            {
                try
                {
                    TcpClient client = Listener.AcceptTcpClient();
                    ThreadPool.QueueUserWorkItem(TratarClienteHttp, client);
                }
                catch
                {
                    if (Encerrando) break;
                }
            }
        }

        private static void TratarClienteHttp(object obj)
        {
            TcpClient client = (TcpClient)obj;
            try
            {
                client.ReceiveTimeout = 30000;
                client.SendTimeout = 30000;
                using (NetworkStream ns = client.GetStream())
                {
                    string metodo, caminho, corpo;
                    Dictionary<string, string> cabecalhos;
                    if (!LerRequisicaoHttp(ns, out metodo, out caminho, out corpo, out cabecalhos)) return;
                    Roteador(ns, metodo, caminho, corpo, cabecalhos);
                }
            }
            catch { }
            finally
            {
                try { client.Close(); } catch { }
            }
        }

        private static bool LerRequisicaoHttp(NetworkStream ns, out string metodo, out string caminho, out string corpo,
            out Dictionary<string, string> cabecalhos)
        {
            metodo = "";
            caminho = "/";
            corpo = "";
            cabecalhos = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            List<byte> headerBytes = new List<byte>(2048);
            int b;
            while ((b = ns.ReadByte()) != -1)
            {
                headerBytes.Add((byte)b);
                int count = headerBytes.Count;
                if (count >= 4 &&
                    headerBytes[count - 4] == 13 && headerBytes[count - 3] == 10 &&
                    headerBytes[count - 2] == 13 && headerBytes[count - 1] == 10)
                {
                    break;
                }
                if (count > 32768) return false;
            }
            if (headerBytes.Count == 0) return false;

            string cabecalho = Encoding.UTF8.GetString(headerBytes.ToArray());
            string[] linhas = cabecalho.Split(new string[] { "\r\n" }, StringSplitOptions.None);
            if (linhas.Length == 0) return false;

            string[] p0 = linhas[0].Split(' ');
            if (p0.Length < 2) return false;
            metodo = p0[0].Trim().ToUpperInvariant();
            caminho = p0[1].Trim();

            for (int i = 1; i < linhas.Length; i++)
            {
                int separador = linhas[i].IndexOf(':');
                if (separador > 0)
                    cabecalhos[linhas[i].Substring(0, separador)] = linhas[i].Substring(separador + 1).Trim();
            }

            string tamanho;
            int contentLength = 0;
            if (cabecalhos.TryGetValue("Content-Length", out tamanho)) int.TryParse(tamanho, out contentLength);
            if (contentLength > 0 && contentLength < 1048576)
            {
                byte[] buf = new byte[contentLength];
                int lidos = 0;
                while (lidos < contentLength)
                {
                    int r = ns.Read(buf, lidos, contentLength - lidos);
                    if (r <= 0) break;
                    lidos += r;
                }
                corpo = Encoding.UTF8.GetString(buf, 0, lidos);
            }
            return true;
        }

        private static void EnviarResposta(NetworkStream ns, int status, string textoStatus, string contentType, byte[] dados)
        {
            StringBuilder sb = new StringBuilder();
            sb.AppendFormat("HTTP/1.1 {0} {1}\r\n", status, textoStatus);
            sb.AppendFormat("Content-Type: {0}\r\n", contentType);
            sb.AppendFormat("Content-Length: {0}\r\n", dados.Length);
            sb.Append("Cache-Control: no-store, no-cache, must-revalidate\r\n");
            sb.Append("X-Content-Type-Options: nosniff\r\n");
            sb.Append("Connection: close\r\n\r\n");
            byte[] hdr = Encoding.ASCII.GetBytes(sb.ToString());
            ns.Write(hdr, 0, hdr.Length);
            if (dados.Length > 0)
            {
                ns.Write(dados, 0, dados.Length);
            }
        }

        private static void EnviarJson(NetworkStream ns, int status, object obj)
        {
            JavaScriptSerializer js = new JavaScriptSerializer();
            js.MaxJsonLength = 8388608;
            string s = js.Serialize(obj);
            EnviarResposta(ns, status, status == 200 ? "OK" : "Error", "application/json; charset=utf-8", Encoding.UTF8.GetBytes(s));
        }

        private static Dictionary<string, object> LerCorpoJson(string corpo)
        {
            if (string.IsNullOrWhiteSpace(corpo)) return new Dictionary<string, object>();
            try
            {
                JavaScriptSerializer js = new JavaScriptSerializer();
                Dictionary<string, object> d = js.Deserialize<Dictionary<string, object>>(corpo);
                return d ?? new Dictionary<string, object>();
            }
            catch
            {
                return new Dictionary<string, object>();
            }
        }

        private static string ValorStr(Dictionary<string, object> d, string chave)
        {
            if (d != null && d.ContainsKey(chave) && d[chave] != null)
            {
                return Convert.ToString(d[chave]);
            }
            return "";
        }

        private static bool ValorBool(Dictionary<string, object> d, string chave)
        {
            if (d != null && d.ContainsKey(chave) && d[chave] != null)
            {
                try { return Convert.ToBoolean(d[chave]); } catch { }
            }
            return false;
        }

        private static Dictionary<string, object> ConsultarMotorApiLocal(string metodo, string rota, string corpo,
            int prazoMs = 30000)
        {
            string python = Path.Combine(Raiz, @".venv\Scripts\python.exe");
            string motor = Path.Combine(Raiz, @"painel\motor_painel.py");
            if (!File.Exists(python) || !File.Exists(motor)) return null;

            try
            {
                JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 8388608 };
                byte[] pedido = Encoding.UTF8.GetBytes(json.Serialize(new
                {
                    metodo = metodo,
                    rota = rota,
                    corpo = LerCorpoJson(corpo)
                }));
                ProcessStartInfo psi = new ProcessStartInfo(python,
                    EscapeArg(motor) + " --api-local --raiz " + EscapeArg(Raiz));
                psi.WorkingDirectory = Raiz;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardInput = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.EnvironmentVariables["PATH"] = GarantirPathSistema("");
                psi.EnvironmentVariables["PYTHONPATH"] = Raiz;
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
#if MODO_DEV
                psi.EnvironmentVariables["CORTELIVE_MODO_DEV"] = "1";
#else
                psi.EnvironmentVariables.Remove("CORTELIVE_MODO_DEV");
#endif
                using (Process p = Process.Start(psi))
                {
                    var saida = p.StandardOutput.ReadToEndAsync();
                    var erros = p.StandardError.ReadToEndAsync();
                    using (Stream entrada = p.StandardInput.BaseStream)
                        entrada.Write(pedido, 0, pedido.Length);
                    if (!p.WaitForExit(prazoMs))
                    {
                        MatarArvoreProcesso(p.Id);
                        return null;
                    }
                    erros.GetAwaiter().GetResult();
                    return p.ExitCode == 0
                        ? json.Deserialize<Dictionary<string, object>>(saida.GetAwaiter().GetResult())
                        : null;
                }
            }
            catch { return null; }
        }

#if MODO_DEV
        private static byte[] PrepararIndexHtmlModoDev(string arquivo)
        {
            string html = File.ReadAllText(arquivo, Encoding.UTF8);
            html = Regex.Replace(html, @"<title>[^<]*</title>", "<title>" + NomeApp + "</title>");
            return Encoding.UTF8.GetBytes(html.Replace("</head>",
                "<link rel=\"stylesheet\" href=\"/dev/dev.css\">\n" +
                "<script>window.CORTELIVE_MODO = 'dev';</script>\n</head>"));
        }

        private static object LogsDev()
        {
            lock (LockSessao)
            {
                return new
                {
                    ok = true, corte = new
                    {
                        rodando = SessaoRodando, estado = SessaoEstado, canal = SessaoCanal,
                        detalhe_servicos = SessaoDetalheServicos, ultima_mensagem = SessaoUltimaMsg,
                        log = string.Join("\n", SessaoLinhasLog.ToArray())
                    }
                };
            }
        }

        private static string VideoLocalDev(string pasta, string arquivo)
        {
            if (string.IsNullOrWhiteSpace(arquivo)) return null;
            try
            {
                string basePasta = Path.Combine(Raiz, pasta) + Path.DirectorySeparatorChar;
                string caminho = Path.GetFullPath(Path.Combine(basePasta, arquivo));
                if (caminho.StartsWith(basePasta, StringComparison.OrdinalIgnoreCase) &&
                    Path.GetExtension(caminho).Equals(".mp4", StringComparison.OrdinalIgnoreCase) && File.Exists(caminho))
                    return caminho;
            }
            catch (ArgumentException) { }
            catch (NotSupportedException) { }
            return null;
        }

        private static bool TratarRotaDev(NetworkStream ns, string metodo, string rota, string alvo, string corpo)
        {
            if (!rota.StartsWith("/api/dev/", StringComparison.Ordinal)) return false;
            if (rota == "/api/dev/resumo" || rota == "/api/dev/configs")
            {
                if (!RepassarAoMotorApiLocal(ns, metodo, alvo, corpo))
                    EnviarJson(ns, 503, new { ok = false, erro = "O motor local está indisponível." });
                return true;
            }
            if (rota == "/api/dev/logs" && metodo == "GET")
            {
                EnviarJson(ns, 200, LogsDev());
                return true;
            }
            if (metodo != "POST") return false;

            Dictionary<string, object> body = LerCorpoJson(corpo);
            string erro = null;
            switch (rota)
            {
                case "/api/dev/atualizacao/checar":
                case "/api/dev/atualizacao/aplicar":
                case "/api/dev/atualizacao/voltar":
                    bool checar = rota.EndsWith("/checar", StringComparison.Ordinal);
                    bool voltar = rota.EndsWith("/voltar", StringComparison.Ordinal);
                    if (!checar && (InstRodando || SessaoRodando))
                    {
                        erro = "Pare a sessão ou aguarde o fim da instalação antes de atualizar.";
                        break;
                    }
                    string canal = ValorStr(body, "canal") == "estavel" ? "estavel" : CanalAtualizacao;
                    if (!checar) CacheImportsOk = null;
                    string modo = voltar ? "-Voltar" : (checar ? "-Checar" : "-Aplicar") + " -Canal " + canal;
                    EnviarJson(ns, 200, ExecutarAtualizador(modo, checar ? 25000 : 900000));
                    return true;
                case "/api/dev/limpar-log":
                    switch (ValorStr(body, "alvo"))
                    {
                        case "corte": lock (LockSessao) SessaoLinhasLog.Clear(); break;
                        default: erro = "Escolha o log a limpar."; break;
                    }
                    break;
                case "/api/dev/abrir-pasta":
                    string pasta;
                    switch (ValorStr(body, "pasta"))
                    {
                        case "cortes": pasta = Path.Combine(Raiz, "cortes"); break;
                        case "logs": pasta = Path.Combine(Raiz, "qa"); break;
                        case "config": pasta = Raiz; break;
                        case "dados_local":
                            pasta = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CorteLive");
                            break;
                        default: pasta = null; break;
                    }
                    if (pasta != null && Directory.Exists(pasta))
                        Process.Start(new ProcessStartInfo(pasta) { UseShellExecute = true });
                    else erro = "Pasta indisponível.";
                    break;
                case "/api/dev/testar-corte":
                    string video = VideoLocalDev("teste_video", ValorStr(body, "arquivo"));
                    double minutos;
                    if (video == null || !double.TryParse(ValorStr(body, "minutos"),
                        System.Globalization.NumberStyles.AllowDecimalPoint,
                        System.Globalization.CultureInfo.InvariantCulture, out minutos) || minutos <= 0)
                        erro = "Escolha o vídeo de teste e a duração.";
                    else
                        erro = IniciarCorte(video, ValorStr(body, "modelo"), ValorStr(body, "legenda"),
                            minutos.ToString(System.Globalization.CultureInfo.InvariantCulture));
                    break;
                case "/api/dev/lancar-versao":
                    string assistente = Path.Combine(Raiz, "lancar_versao.cmd");
                    if (!File.Exists(assistente)) erro = "Assistente de lançamento indisponível.";
                    else Process.Start(new ProcessStartInfo(assistente, ValorBool(body, "simular") ? "-Simular" : "")
                    {
                        WorkingDirectory = Raiz,
                        UseShellExecute = true
                    });
                    break;
                default: return false;
            }
            EnviarJson(ns, erro == null ? 200 : 400, new { ok = erro == null, erro = erro });
            return true;
        }
#endif

        private static bool RepassarAoMotorApiLocal(NetworkStream ns, string metodo, string rota, string corpo,
            int prazoMs = 30000)
        {
            Dictionary<string, object> resposta = ConsultarMotorApiLocal(metodo, rota, corpo, prazoMs);
            object valorStatus, dados;
            if (resposta == null || !resposta.TryGetValue("status", out valorStatus) || !(valorStatus is int))
                return false;
            int status = (int)valorStatus;
            if (resposta.TryGetValue("dados", out dados))
            {
                EnviarJson(ns, status, dados);
                return true;
            }
            if (status == 200 && ValorStr(resposta, "tipo") == "image/jpeg")
            {
                byte[] imagem = Convert.FromBase64String(ValorStr(resposta, "base64"));
                EnviarResposta(ns, 200, "OK", "image/jpeg", imagem);
                return true;
            }
            return false;
        }

        private static void Roteador(NetworkStream ns, string metodo, string caminhoBruto, string corpo,
            Dictionary<string, string> cabecalhos)
        {
            string rota = caminhoBruto;
            int idxQ = rota.IndexOf('?');
            if (idxQ >= 0) rota = rota.Substring(0, idxQ);

            if (rota == "/api/gpt/status" || rota == "/api/gpt/conectar")
            {
                string host, origem;
                string hostPainel = "127.0.0.1:" + Porta;
                cabecalhos.TryGetValue("Host", out host);
                cabecalhos.TryGetValue("Origin", out origem);
                if (host != hostPainel || ((metodo == "POST" || origem != null) && origem != "http://" + hostPainel))
                    EnviarJson(ns, 403, new { ok = false, erro = "Abra a conexão com GPT pelo painel do CorteLive." });
                else if (!RepassarAoMotorApiLocal(ns, metodo, caminhoBruto, corpo, 45000))
                    EnviarJson(ns, 503, new { ok = false, erro = "O motor local está indisponível." });
                return;
            }

            if (rota == "/api/ping")
            {
                EnviarJson(ns, 200, new { ok = true, versao = LerVersao(), exe_versao = VersaoExe(), pid = Process.GetCurrentProcess().Id });
                return;
            }
            if (rota == "/api/heartbeat")
            {
                UltimoHeartbeat = DateTime.UtcNow;
                JanelaAbertaAlgumaVez = true;
                JanelaFechadaEm = null;
                EnviarJson(ns, 200, new { ok = true });
                return;
            }
            if (rota == "/api/janela-fechada")
            {
                if (JanelaAbertaAlgumaVez) JanelaFechadaEm = DateTime.UtcNow;
                EnviarJson(ns, 200, new { ok = true });
                return;
            }
            if (rota == "/api/encerrar" && metodo == "POST")
            {
                EnviarJson(ns, 200, new { ok = true });
                Encerrando = true;
                return;
            }
            if (rota == "/api/estado")
            {
                UltimoHeartbeat = DateTime.UtcNow;
                JanelaAbertaAlgumaVez = true;
                JanelaFechadaEm = null;
                EnviarJson(ns, 200, MontarEstado());
                return;
            }
            if (rota == "/api/instalar" && metodo == "POST")
            {
                IniciarInstalacao();
                EnviarJson(ns, 200, new { ok = true });
                return;
            }
            if (rota == "/api/log-instalacao")
            {
                EnviarJson(ns, 200, new { ok = true, log = LerLogInstalacaoCompleto() });
                return;
            }
            if (rota == "/api/cortar" && metodo == "POST")
            {
                Dictionary<string, object> body = LerCorpoJson(corpo);
                string erro = IniciarCorte(ValorStr(body, "fonte"), ValorStr(body, "modelo"), ValorStr(body, "legenda"));
                if (!string.IsNullOrEmpty(erro))
                    EnviarJson(ns, 400, new { ok = false, erro = erro });
                else
                    EnviarJson(ns, 200, new { ok = true });
                return;
            }
            if (rota == "/api/parar" && metodo == "POST")
            {
                PararSessaoInterno(false);
                EnviarJson(ns, 200, new { ok = true });
                return;
            }
            if (rota == "/api/parar-live" && metodo == "POST")
            {
                Dictionary<string, object> body = LerCorpoJson(corpo);
                string erro = PararLiveInterno(ValorStr(body, "id"));
                EnviarJson(ns, erro == null ? 200 : 400, new { ok = erro == null, erro = erro });
                return;
            }
            if (rota == "/api/abrir-video" && metodo == "POST")
            {
                Dictionary<string, object> body = LerCorpoJson(corpo);
                string erro = AbrirVideoCorte(ValorStr(body, "caminho"));
                EnviarJson(ns, erro == null ? 200 : 400, new { ok = erro == null, erro = erro });
                return;
            }
            if (rota == "/api/abrir-pasta" && metodo == "POST")
            {
                Dictionary<string, object> body = LerCorpoJson(corpo);
                string erro = AbrirPastaCortes(ValorStr(body, "caminho"));
                EnviarJson(ns, erro == null ? 200 : 400, new { ok = erro == null, erro = erro });
                return;
            }
            if (rota == "/api/escolher-arquivo" && metodo == "POST")
            {
                EnviarJson(ns, 200, new { ok = true, caminho = EscolherVideoDialogo() ?? "" });
                return;
            }

            // Pastas por nota (Configurações): janela do Windows para escolher a pasta de uma faixa
            if (rota == "/api/escolher-pasta" && metodo == "POST")
            {
                EnviarJson(ns, 200, new { ok = true, caminho = EscolherPastaDialogo() ?? "" });
                return;
            }

            // Miniatura e lista de cortes via motor_painel.py --api-local quando disponível
            if (rota == "/api/miniatura" && metodo == "GET")
            {
                if (RepassarAoMotorApiLocal(ns, metodo, caminhoBruto, corpo)) return;
                EnviarResposta(ns, 404, "Not Found", "text/plain; charset=utf-8", new byte[0]);
                return;
            }
            if (rota == "/api/cortes" && metodo == "GET")
            {
                EnviarJson(ns, 200, new { ok = true, cortes = ListarCortes() });
                return;
            }

            if (rota == "/api/historico/apagar" && metodo == "POST")
            {
                if (InstRodando || SessaoRodando)
                {
                    EnviarJson(ns, 409, new { ok = false, erro = "Há uma live em andamento. Pare a live antes de apagar o histórico." });
                    return;
                }
                if (!RepassarAoMotorApiLocal(ns, metodo, caminhoBruto, corpo))
                    EnviarJson(ns, 503, new { ok = false, erro = "O motor local está indisponível." });
                return;
            }

            // Editor de foco do corte (v1.0.16) e modo do corte (v1.0.17): estado, quadro (JPEG) e refazer/automático/baixar/modo (começam em segundo plano)
            if (rota == "/api/foco/estado" || rota == "/api/foco/quadro" || rota == "/api/foco/refazer" ||
                rota == "/api/foco/automatico" || rota == "/api/foco/baixar" || rota == "/api/foco/modo" ||
                rota == "/api/foco-live/pendente" || rota == "/api/foco-live/quadro" ||
                rota == "/api/foco-live/confirmar" || rota == "/api/foco-live/canais" ||
                rota == "/api/foco-live/esquecer")
            {
                if (!RepassarAoMotorApiLocal(ns, metodo, caminhoBruto, corpo, 30000))
                    EnviarJson(ns, 503, new { ok = false, erro = "O motor local está indisponível." });
                return;
            }

            if (rota == "/api/drive/detectar" || rota == "/api/drive/config" ||
                rota == "/api/drive/enviar" || rota == "/api/cortes/excluir" ||
                rota == "/api/enviar-erro" ||
                // v1.0.17: aprendizado com o "Não quero" e aviso de espaço em disco
                rota == "/api/aprendizado" || rota == "/api/aprendizado/zerar" ||
                rota == "/api/espaco" || rota == "/api/espaco/limpar")
            {
                // O envio manual aguarda a cópia integral na pasta sincronizada.
                int prazoMs = rota == "/api/drive/enviar" && metodo == "POST" ? 15 * 60 * 1000
                    : rota == "/api/enviar-erro" || rota.StartsWith("/api/espaco", StringComparison.Ordinal) ? 90000 : 30000;
                if (!RepassarAoMotorApiLocal(ns, metodo, caminhoBruto, corpo, prazoMs))
                    EnviarJson(ns, 503, new { ok = false, erro = "O motor local está indisponível." });
                return;
            }

            // Atualização delegada a instalador\atualizar.ps1 (-Checar | -Aplicar | -Voltar)
            // CorteLive.exe (rapaziada) SEMPRE consulta apenas o canal estável (-Canal estavel).
            if (rota == "/api/atualizacao/checar")
            {
                EnviarJson(ns, 200, ExecutarAtualizador("-Checar -Canal " + CanalAtualizacao, 25000));
                return;
            }
            if (rota == "/api/atualizacao/aplicar" && metodo == "POST")
            {
                if (InstRodando || SessaoRodando)
                {
                    EnviarJson(ns, 400, new { etapa = "erro", pct = 100, erro = "Pare a sessão ou aguarde o fim da instalação antes de atualizar." });
                    return;
                }
                CacheImportsOk = null;
                EnviarJson(ns, 200, IniciarAtualizacaoEmSegundoPlano("-Aplicar -Canal " + CanalAtualizacao, 900000));
                return;
            }
            if (rota == "/api/atualizacao/voltar" && metodo == "POST")
            {
                CacheImportsOk = null;
                EnviarJson(ns, 200, ExecutarAtualizador("-Voltar", 600000));
                return;
            }

            // Preferências de corte (duração mínima), guardadas pelo motor em painel_config.json
            if (rota == "/api/config-postagem")
            {
                if (!RepassarAoMotorApiLocal(ns, metodo, caminhoBruto, corpo))
                    EnviarJson(ns, 503, new { ok = false, erro = "O motor local está indisponível." });
                return;
            }

#if MODO_DEV
            if (rota.StartsWith("/api/dev/", StringComparison.OrdinalIgnoreCase) ||
                rota.StartsWith("/dev/", StringComparison.OrdinalIgnoreCase))
            {
                if (TratarRotaDev(ns, metodo, rota, caminhoBruto, corpo)) return;
            }
#else
            // Bloqueia qualquer acesso a /dev/ ou /api/dev/ no CorteLive.exe da rapaziada
            if (rota == "/dev" ||
                rota.StartsWith("/dev/", StringComparison.OrdinalIgnoreCase) ||
                rota.StartsWith("/api/dev/", StringComparison.OrdinalIgnoreCase))
            {
                EnviarResposta(ns, 404, "Not Found", "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Não encontrado"));
                return;
            }
#endif

            // Arquivos estáticos de painel\
            string rel = rota == "/" ? "index.html" : Uri.UnescapeDataString(rota.TrimStart('/')).Replace('/', '\\');
#if MODO_DEV
            if (rel == @"dev\icone-dev.ico") rel = @"img\cortelive-dev.ico";
            if (rel == @"dev\icone-dev.png") rel = @"img\cortelive-icone-dev.png";
#endif
            string pastaPainel = Path.GetFullPath(Path.Combine(Raiz, "painel")) + Path.DirectorySeparatorChar;
            string arqStatic = Path.GetFullPath(Path.Combine(pastaPainel, rel));
            bool permitido = arqStatic.StartsWith(pastaPainel, StringComparison.OrdinalIgnoreCase);
#if !MODO_DEV
            permitido = permitido && !arqStatic.StartsWith(Path.Combine(pastaPainel, "dev") + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase);
#endif
            if (permitido && File.Exists(arqStatic))
            {
                string ext = Path.GetExtension(arqStatic).ToLowerInvariant();
#if MODO_DEV
                if (ext == ".html" && string.Equals(Path.GetFileName(arqStatic), "index.html", StringComparison.OrdinalIgnoreCase))
                {
                    byte[] htmlDev = PrepararIndexHtmlModoDev(arqStatic);
                    EnviarResposta(ns, 200, "OK", "text/html; charset=utf-8", htmlDev);
                    return;
                }
#endif
                string mime = "application/octet-stream";
                if (ext == ".html") mime = "text/html; charset=utf-8";
                else if (ext == ".css") mime = "text/css; charset=utf-8";
                else if (ext == ".js") mime = "application/javascript; charset=utf-8";
                else if (ext == ".svg") mime = "image/svg+xml";
                else if (ext == ".json") mime = "application/json; charset=utf-8";
                else if (ext == ".png") mime = "image/png";
                else if (ext == ".webp") mime = "image/webp";
                else if (ext == ".ico") mime = "image/x-icon";
                else if (ext == ".jpg" || ext == ".jpeg") mime = "image/jpeg";
                else if (ext == ".woff2") mime = "font/woff2";
                else if (ext == ".ttf") mime = "font/ttf";
                EnviarResposta(ns, 200, "OK", mime, File.ReadAllBytes(arqStatic));
                return;
            }

            EnviarResposta(ns, 404, "Not Found", "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Não encontrado"));
        }

        // =========================================================================
        // Estado Geral, Verificação de Instalação e Leitura dos Cortes
        // =========================================================================
        private static string LerVersao()
        {
            string arq = Path.Combine(Raiz, "VERSION");
            if (File.Exists(arq))
            {
                try
                {
                    string v = File.ReadAllText(arq, Encoding.UTF8).Trim().TrimStart('v', 'V');
                    if (!string.IsNullOrEmpty(v)) return v;
                }
                catch { }
            }
            string initPy = Path.Combine(Raiz, @"cortelive\__init__.py");
            if (File.Exists(initPy))
            {
                try
                {
                    Match m = Regex.Match(File.ReadAllText(initPy, Encoding.UTF8), "__version__\\s*=\\s*\"([^\"]+)\"");
                    if (m.Success) return m.Groups[1].Value.Trim();
                }
                catch { }
            }
            return "0.1.0";
        }

        private static string GarantirPathSistema(string pathExtra)
        {
            string sys32 = Environment.GetFolderPath(Environment.SpecialFolder.System);
            string win = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
            string wbem = Path.Combine(sys32, "Wbem");
            string psDir = Path.Combine(sys32, @"WindowsPowerShell\v1.0");
            string atual = Environment.GetEnvironmentVariable("PATH") ?? "";
            StringBuilder sb = new StringBuilder();
            if (!string.IsNullOrEmpty(pathExtra)) sb.Append(pathExtra).Append(";");
            sb.Append(atual);
            foreach (string ess in new string[] { sys32, win, wbem, psDir })
            {
                if (!string.IsNullOrEmpty(ess) && atual.IndexOf(ess, StringComparison.OrdinalIgnoreCase) < 0)
                {
                    sb.Append(";").Append(ess);
                }
            }
            return sb.ToString();
        }

        private static string CaminhoPowerShell()
        {
            string sys32 = Environment.GetFolderPath(Environment.SpecialFolder.System);
            string cand = Path.Combine(sys32, @"WindowsPowerShell\v1.0\powershell.exe");
            if (File.Exists(cand)) return cand;
            return "powershell.exe";
        }

        private static bool TemFFmpeg()
        {
            string local = Path.Combine(Raiz, @"ffmpeg\bin\ffmpeg.exe");
            string localProbe = Path.Combine(Raiz, @"ffmpeg\bin\ffprobe.exe");
            if (File.Exists(local) && File.Exists(localProbe)) return true;

            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string p in path.Split(';'))
            {
                string limpo = p.Trim();
                if (string.IsNullOrEmpty(limpo)) continue;
                try
                {
                    if (File.Exists(Path.Combine(limpo, "ffmpeg.exe")) &&
                        File.Exists(Path.Combine(limpo, "ffprobe.exe"))) return true;
                }
                catch { }
            }
            return false;
        }

        private static long TamanhoPastaBytes(string pasta)
        {
            if (!Directory.Exists(pasta)) return 0;
            long soma = 0;
            try
            {
                foreach (string arq in Directory.GetFiles(pasta, "*", SearchOption.AllDirectories))
                {
                    try { soma += new FileInfo(arq).Length; } catch { }
                }
            }
            catch { }
            return soma;
        }

        private static bool EstaInstalado()
        {
            if (InstRodando) return false;
#if MODO_DEV
            // CorteLive Dev roda direto na pasta do projeto sem exigir instalador
            if (File.Exists(Path.Combine(Raiz, @"cortelive\__init__.py"))) return true;
#endif
            string vpy = Path.Combine(Raiz, @".venv\Scripts\python.exe");
            string hub = Path.Combine(Raiz, @"modelos\hub");

            if (!File.Exists(vpy) || !TemFFmpeg())
            {
                CacheImportsOk = false;
                return false;
            }
            if (TamanhoPastaBytes(hub) < 100L * 1024L * 1024L)
            {
                CacheImportsOk = false;
                return false;
            }
            if (CacheImportsOk.HasValue && CacheImportsOk.Value)
            {
                return true;
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = vpy;
                psi.Arguments = "-c \"import faster_whisper,streamlink,yt_dlp,cv2,numpy,requests\"";
                psi.WorkingDirectory = Raiz;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi))
                {
                    p.WaitForExit(15000);
                    CacheImportsOk = (p.HasExited && p.ExitCode == 0);
                    return CacheImportsOk.Value;
                }
            }
            catch
            {
                CacheImportsOk = false;
                return false;
            }
        }

        private static string LerModoDescricao()
        {
            string arq = Path.Combine(Raiz, @"instalador\modo.txt");
            if (!File.Exists(arq)) return "Ambiente local pronto para cortar";
            try
            {
                foreach (string l in File.ReadAllLines(arq, Encoding.UTF8))
                {
                    if (l.StartsWith("modo="))
                    {
                        string m = l.Substring(5).Trim().ToLowerInvariant();
                        if (m == "nvidia") return "Modo NVIDIA ativo (transcrição CUDA + vídeo NVENC na placa)";
                        if (m == "amd") return "Modo AMD ativo (fala no processador + vídeo AMF na placa)";
                        if (m == "intel") return "Modo Intel ativo (fala no processador + vídeo QuickSync na placa)";
                        return "Modo Processador (CPU) ativo";
                    }
                }
            }
            catch { }
            return "Pronto para cortar";
        }

        private static object ListarCortes()
        {
            Dictionary<string, object> resposta = ConsultarMotorApiLocal("GET", "/api/cortes", "");
            object dados, cortes;
            if (resposta != null && resposta.TryGetValue("dados", out dados))
            {
                Dictionary<string, object> lista = dados as Dictionary<string, object>;
                if (lista != null && lista.TryGetValue("cortes", out cortes) && cortes is System.Collections.IList)
                    return cortes;
            }
            return ListarCortesSemMotor();
        }

        private static List<Dictionary<string, object>> ListarCortesSemMotor()
        {
            List<Dictionary<string, object>> resultado = new List<Dictionary<string, object>>();
            string pastaCortes = Path.Combine(Raiz, "cortes");
            if (!Directory.Exists(pastaCortes)) return resultado;

            JavaScriptSerializer js = new JavaScriptSerializer();
            List<string> mp4s = new List<string>();
            Stack<string> pendentes = new Stack<string>();
            pendentes.Push(pastaCortes);
            while (pendentes.Count > 0)
            {
                string pasta = pendentes.Pop();
                try
                {
                    mp4s.AddRange(Directory.GetFiles(pasta, "*.mp4"));
                    foreach (string subpasta in Directory.GetDirectories(pasta))
                    {
                        string nome = Path.GetFileName(subpasta);
                        if (nome.StartsWith(".") || nome.StartsWith("_") ||
                            nome.Equals("sem legenda", StringComparison.OrdinalIgnoreCase)) continue;
                        if ((File.GetAttributes(subpasta) & FileAttributes.ReparsePoint) == 0)
                            pendentes.Push(subpasta);
                    }
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }

            mp4s.Sort(delegate(string a, string b)
            {
                DateTime ta = DateTime.MinValue, tb = DateTime.MinValue;
                try { ta = File.GetLastWriteTimeUtc(a); } catch { }
                try { tb = File.GetLastWriteTimeUtc(b); } catch { }
                return tb.CompareTo(ta);
            });

            foreach (string mp4 in mp4s)
            {
                if (!string.Equals(Path.GetExtension(mp4), ".mp4", StringComparison.OrdinalIgnoreCase)) continue;
                if (mp4.EndsWith("_sem_legenda.mp4", StringComparison.OrdinalIgnoreCase)) continue;

                Dictionary<string, object> item = new Dictionary<string, object>();
                item["caminho"] = mp4;
                item["nome_arquivo"] = Path.GetFileName(mp4);

                string pastaSessao = Path.GetDirectoryName(mp4);
                string dirSessao = Path.GetFileName(pastaSessao);
                item["pasta"] = pastaSessao;
                string semLegenda = Path.Combine(pastaSessao, "sem legenda", Path.GetFileName(mp4));
                if (!File.Exists(semLegenda))
                    semLegenda = Path.Combine(pastaSessao, Path.GetFileNameWithoutExtension(mp4) + "_sem_legenda.mp4");
                item["caminho_sem_legenda"] = File.Exists(semLegenda) ? semLegenda : null;
                string canal = dirSessao;
                Match mCanal = Regex.Match(dirSessao, @"^\d{4}-\d{2}-\d{2}(?:_\d{4}_| \d{2}h\d{2} - )(.+)$");
                if (mCanal.Success) canal = mCanal.Groups[1].Value;
                item["canal"] = canal;

                try { item["data_hora"] = File.GetLastWriteTime(mp4).ToString("dd/MM HH:mm"); }
                catch { item["data_hora"] = ""; }

                string jsonMeta = Path.Combine(pastaSessao, "_interno", Path.GetFileNameWithoutExtension(mp4) + ".json");
                if (!File.Exists(jsonMeta)) jsonMeta = Path.ChangeExtension(mp4, ".json");
                if (File.Exists(jsonMeta))
                {
                    try
                    {
                        Dictionary<string, object> meta = js.Deserialize<Dictionary<string, object>>(
                            File.ReadAllText(jsonMeta, Encoding.UTF8));
                        if (meta != null)
                        {
                            if (meta.ContainsKey("titulo_sugerido") && meta["titulo_sugerido"] != null)
                                item["titulo"] = Convert.ToString(meta["titulo_sugerido"]);
                            if (meta.ContainsKey("duracao") && meta["duracao"] != null)
                                item["duracao"] = meta["duracao"];
                            object juiz, notaGPT;
                            if (meta.TryGetValue("juiz", out juiz) && ((juiz as string) == "api" || (juiz as string) == "chatgpt")
                                && meta.TryGetValue("nota_juiz", out notaGPT) && notaGPT is int && (int)notaGPT >= 0 && (int)notaGPT <= 10)
                                item["nota_juiz"] = notaGPT;
                            if (meta.ContainsKey("momento_hms") && meta["momento_hms"] != null)
                                item["inicio_hms"] = Convert.ToString(meta["momento_hms"]);
                            else if (meta.ContainsKey("inicio_hms") && meta["inicio_hms"] != null)
                                item["inicio_hms"] = Convert.ToString(meta["inicio_hms"]);
                            if (meta.ContainsKey("motivo") && meta["motivo"] != null)
                                item["motivo"] = Convert.ToString(meta["motivo"]);
                            if (meta.ContainsKey("modelo_usado") && meta["modelo_usado"] != null)
                                item["modelo"] = Convert.ToString(meta["modelo_usado"]);
                        }
                    }
                    catch { }
                }

                resultado.Add(item);
                if (resultado.Count >= 60) break;
            }

            return resultado;
        }

        private static object MontarEstado()
        {
            bool instalado = EstaInstalado();

            object blocoInst;
            lock (LockInst)
            {
                int inicio = Math.Max(0, InstLinhasLog.Count - 30);
                string logRec = string.Join("\n", InstLinhasLog.GetRange(inicio, InstLinhasLog.Count - inicio).ToArray());
                blocoInst = new
                {
                    rodando = InstRodando,
                    etapa = InstEtapa,
                    porcentagem = Math.Round(InstPct, 0),
                    titulo_etapa = InstTituloEtapa,
                    detalhe = InstDetalhe,
                    erro = InstErro,
                    o_que_fazer = InstOQueFazer,
                    log_recente = logRec
                };
            }

            object blocoSessao;
            lock (LockSessao)
            {
                int iniS = Math.Max(0, SessaoLinhasLog.Count - 25);
                string logS = string.Join("\n", SessaoLinhasLog.GetRange(iniS, SessaoLinhasLog.Count - iniS).ToArray());
                string logCompleto = string.Join("\n", SessaoLinhasLog.ToArray());
                blocoSessao = new
                {
                    rodando = SessaoRodando,
                    estado = SessaoEstado,
                    canal = SessaoCanal,
                    live_s = SessaoLiveS,
                    atraso_s = SessaoAtrasoS,
                    atraso_canal = SessaoAtrasoCanal,
                    ultimo_corte_ha_s = SessaoUltimoCorteHaS,
                    cortes_sessao = SessaoCortes,
                    ultima_mensagem = SessaoUltimaMsg,
                    detalhe_servicos = SessaoDetalheServicos,
                    erro = SessaoErro,
                    log_recente = logS,
                    log_completo = logCompleto,
                    lives = SessaoLives.ToArray()
                };
            }

            return new
            {
                ok = true,
                versao = LerVersao(),
                pasta_raiz = Raiz,
                instalado = instalado,
                modo_descricao = instalado ? LerModoDescricao() : "",
                instalacao = blocoInst,
                sessao = blocoSessao,
                atualizacao = BlocoAtualizacao(),
                cortes = instalado ? ListarCortes() : new List<Dictionary<string, object>>()
            };
        }

        // =========================================================================
        // Instalação em Segundo Plano (instalador\instalar.ps1 -SemPerguntas)
        // =========================================================================
        private static void IniciarInstalacao()
        {
            lock (LockInst)
            {
                if (InstRodando) return;
                InstRodando = true;
                InstEtapa = 1;
                InstPct = 2.0;
                InstTituloEtapa = "1/6 Preparando instalação";
                InstDetalhe = "Verificando espaço em disco e downloads oficiais…";
                InstErro = null;
                InstOQueFazer = null;
                InstLinhasLog.Clear();
                CacheImportsOk = null;
            }

            Thread th = new Thread(ExecutarInstalacaoThread);
            th.IsBackground = true;
            th.Start();
        }

        private static void ExecutarInstalacaoThread()
        {
            string script = Path.Combine(Raiz, @"instalador\instalar.ps1");
            if (!File.Exists(script))
            {
                lock (LockInst)
                {
                    InstRodando = false;
                    InstErro = "Arquivo instalador\\instalar.ps1 não encontrado na pasta.";
                    InstOQueFazer = "Baixe novamente o CorteLive.exe do GitHub Releases.";
                }
                return;
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = CaminhoPowerShell();
                psi.Arguments = String.Format("-NoProfile -ExecutionPolicy Bypass -File \"{0}\" -SemPerguntas", script);
                psi.WorkingDirectory = Raiz;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;
                psi.EnvironmentVariables["PATH"] = GarantirPathSistema("");

                Process p = new Process();
                p.StartInfo = psi;
                p.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data != null) ProcessarLinhaInstalacao(e.Data);
                };
                p.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrWhiteSpace(e.Data)) ProcessarLinhaInstalacao(e.Data);
                };

                lock (LockInst) { ProcInstalacao = p; }
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                p.WaitForExit();

                int codigo = p.ExitCode;
                lock (LockInst)
                {
                    ProcInstalacao = null;
                    InstRodando = false;
                    if (codigo == 0)
                    {
                        InstEtapa = 7;
                        InstPct = 100.0;
                        InstTituloEtapa = "Instalação concluída!";
                        InstDetalhe = "Tudo pronto para cortar lives.";
                        InstErro = null;
                    }
                    else if (string.IsNullOrEmpty(InstErro))
                    {
                        InstErro = String.Format("A instalação não terminou (código {0}).", codigo);
                        InstOQueFazer = "Confira sua internet e clique em Tentar novamente — ele continua de onde parou.";
                    }
                }
            }
            catch (Exception ex)
            {
                lock (LockInst)
                {
                    ProcInstalacao = null;
                    InstRodando = false;
                    InstErro = "Erro ao iniciar o instalador: " + ex.Message;
                }
            }
        }

        private static void ProcessarLinhaInstalacao(string linhaBruta)
        {
            string linha = linhaBruta.Trim();
            if (string.IsNullOrEmpty(linha) || linha.StartsWith("#")) return;
            if (linha.StartsWith("Warning:", StringComparison.OrdinalIgnoreCase)) return;

            lock (LockInst)
            {
                InstLinhasLog.Add(linha);
                if (InstLinhasLog.Count > 250) InstLinhasLog.RemoveAt(0);

                Match mPasso = Regex.Match(linha, @"^>>\s*(\d)/6\s+(.+)$");
                if (mPasso.Success)
                {
                    int n = int.Parse(mPasso.Groups[1].Value);
                    InstEtapa = n;
                    InstTituloEtapa = String.Format("Etapa {0} de 6 · {1}", n, mPasso.Groups[2].Value.Trim());
                    double[] marcos = new double[] { 0, 4, 18, 44, 58, 76, 96 };
                    if (n >= 1 && n <= 6 && InstPct < marcos[n]) InstPct = marcos[n];
                    return;
                }

                if (linha.StartsWith("ERRO:", StringComparison.OrdinalIgnoreCase))
                {
                    InstErro = linha.Substring(5).Trim();
                    return;
                }
                if (linha.StartsWith("O que fazer:", StringComparison.OrdinalIgnoreCase))
                {
                    InstOQueFazer = linha.Substring(12).Trim();
                    return;
                }

                InstDetalhe = linha;
                if (linha.StartsWith("OK:", StringComparison.OrdinalIgnoreCase))
                {
                    double[] tetos = new double[] { 0, 17, 43, 57, 75, 95, 99 };
                    if (InstEtapa >= 1 && InstEtapa <= 6 && InstPct < tetos[InstEtapa])
                    {
                        InstPct = tetos[InstEtapa];
                    }
                }
            }
        }

        private static void LoopMonitorProgressoInstalacao()
        {
            while (!Encerrando)
            {
                Thread.Sleep(1500);
                bool rodando;
                int etapa;
                lock (LockInst)
                {
                    rodando = InstRodando;
                    etapa = InstEtapa;
                }
                if (!rodando) continue;

                try
                {
                    double sugerido = 0;
                    if (etapa == 1)
                    {
                        string pyTar = Path.Combine(Raiz, @"_downloads\python.tar.gz");
                        if (File.Exists(pyTar))
                        {
                            double mb = new FileInfo(pyTar).Length / (1024.0 * 1024.0);
                            sugerido = 4.0 + Math.Min(13.0, (mb / 22.0) * 13.0);
                        }
                    }
                    else if (etapa == 2)
                    {
                        string sp = Path.Combine(Raiz, @".venv\Lib\site-packages");
                        if (Directory.Exists(sp))
                        {
                            int dirs = Directory.GetDirectories(sp).Length;
                            sugerido = 18.0 + Math.Min(24.0, (dirs / 45.0) * 24.0);
                        }
                    }
                    else if (etapa == 3)
                    {
                        string ffZip = Path.Combine(Raiz, @"_downloads\ffmpeg.zip");
                        if (File.Exists(ffZip))
                        {
                            double mb = new FileInfo(ffZip).Length / (1024.0 * 1024.0);
                            sugerido = 44.0 + Math.Min(12.0, (mb / 90.0) * 12.0);
                        }
                    }
                    else if (etapa == 4)
                    {
                        string nv = Path.Combine(Raiz, @".venv\Lib\site-packages\nvidia");
                        if (Directory.Exists(nv))
                        {
                            double mb = TamanhoPastaBytes(nv) / (1024.0 * 1024.0);
                            sugerido = 58.0 + Math.Min(16.0, (mb / 950.0) * 16.0);
                        }
                    }
                    else if (etapa == 5)
                    {
                        double mb = TamanhoPastaBytes(Path.Combine(Raiz, "modelos")) / (1024.0 * 1024.0);
                        sugerido = 76.0 + Math.Min(19.0, (mb / 1800.0) * 19.0);
                    }

                    lock (LockInst)
                    {
                        if (InstRodando && sugerido > InstPct && sugerido < 99.0) InstPct = sugerido;
                    }
                }
                catch { }
            }
        }

        private static string LerLogInstalacaoCompleto()
        {
            StringBuilder sb = new StringBuilder();
            string arqLog = Path.Combine(Raiz, "instalacao_log.txt");
            if (File.Exists(arqLog))
            {
                try { sb.AppendLine(File.ReadAllText(arqLog, Encoding.UTF8)); } catch { }
            }
            lock (LockInst)
            {
                if (InstLinhasLog.Count > 0)
                {
                    sb.AppendLine("--- Saída recente do instalador ---");
                    sb.AppendLine(string.Join("\n", InstLinhasLog.ToArray()));
                }
            }
            return sb.ToString().Trim();
        }

        // =========================================================================
        // Sessão de Corte ao Vivo (painel\motor_painel.py no .venv)
        // =========================================================================
        private static List<string> SepararFontes(string entrada)
        {
            List<string> lista = new List<string>();
            if (string.IsNullOrWhiteSpace(entrada)) return lista;
            string limpo = entrada.Trim();
            string semAspas = limpo.Trim('"');
            if (File.Exists(semAspas) || File.Exists(Path.Combine(Raiz, semAspas)))
            {
                lista.Add(semAspas);
                return lista;
            }
            foreach (Match m in Regex.Matches(limpo, "\"([^\"]+)\"|([^\\s,;]+)"))
            {
                string val = m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value;
                if (!string.IsNullOrWhiteSpace(val)) lista.Add(val.Trim());
            }
            return lista;
        }

        private static string EscapeArg(string s)
        {
            if (string.IsNullOrEmpty(s)) return "\"\"";
            return "\"" + Regex.Replace(s, @"(\\*)(\""|$)", delegate(Match m)
            {
                string barras = m.Groups[1].Value;
                string aspas = m.Groups[2].Value;
                return barras + barras + (aspas.Length > 0 ? "\\\"" : "");
            }) + "\"";
        }

        private static string IniciarCorte(string fonteBruta, string modelo, string legenda, string minutos = null)
        {
            List<string> fontes = SepararFontes(fonteBruta);
            if (fontes.Count == 0) return "Informe o link da live ou do vídeo, ou escolha um arquivo do PC.";

            string mod = string.IsNullOrWhiteSpace(modelo) ? "cam-em-cima" : modelo.Trim();
            string leg = string.IsNullOrWhiteSpace(legenda) ? "opus" : legenda.Trim();
            string vpy = Path.Combine(Raiz, @".venv\Scripts\python.exe");
            string motor = Path.Combine(Raiz, @"painel\motor_painel.py");
            if (!File.Exists(vpy)) return "O CorteLive ainda não foi instalado nesta pasta.";

            lock (LockSessao)
            {
                if (SessaoRodando) return AdicionarLiveNaSessao(fontes, mod, leg);
                SessaoRodando = true; SessaoLives.Clear();
                SessaoEstado = "iniciando";
                SessaoCanal = "";
                SessaoLiveS = 0.0;
                SessaoAtrasoS = null;
                SessaoAtrasoCanal = null;
                SessaoUltimoCorteHaS = null;
                SessaoCortes = 0;
                SessaoUltimaMsg = "Preparando motor de corte e modelo de voz…";
                SessaoDetalheServicos = "";
                SessaoErro = null;
                SessaoLinhasLog.Clear();
            }

            string extraArgs = "";
            string modoArq = Path.Combine(Raiz, @"instalador\modo.txt");
            if (File.Exists(modoArq))
            {
                try
                {
                    foreach (string l in File.ReadAllLines(modoArq, Encoding.UTF8))
                    {
                        if (l.StartsWith("args=")) extraArgs = l.Substring(5).Trim();
                    }
                }
                catch { }
            }

            StringBuilder argsBuilder = new StringBuilder();
            argsBuilder.Append(EscapeArg(motor));
            argsBuilder.Append(" --modelo ").Append(EscapeArg(mod));
            argsBuilder.Append(" --legenda ").Append(EscapeArg(leg));
            if (!string.IsNullOrEmpty(extraArgs)) argsBuilder.Append(" ").Append(extraArgs);
            if (!string.IsNullOrEmpty(minutos)) argsBuilder.Append(" --minutos ").Append(minutos);
            argsBuilder.Append(" --");
            foreach (string f in fontes) argsBuilder.Append(" ").Append(EscapeArg(f));

            Thread th = new Thread(delegate() { ExecutarSessaoThread(vpy, argsBuilder.ToString()); });
            th.IsBackground = true;
            th.Start();
            return null;
        }

        // Sessão já em andamento: manda as novas lives ao mesmo motor (cada uma ganha seu cartão e seu Parar).
        // Chamado com LockSessao já travado.
        private static string AdicionarLiveNaSessao(List<string> fontes, string modelo, string legenda)
        {
            Process p = ProcSessao;
            if (p == null) return "A sessão ainda está iniciando. Tente de novo em instantes.";
            if (SessaoEstado == "encerrando" || SessaoEstado == "encerrada")
                return "A sessão está finalizando. Aguarde terminar para iniciar outra live.";
            try
            {
                string opcoes = HexUtf8(modelo) + " " + HexUtf8(legenda);
                foreach (string f in fontes)
                {
                    p.StandardInput.WriteLine("adicionar-live " + HexUtf8(f) + " " + opcoes);
                }
                p.StandardInput.Flush();
            }
            catch { return "Não foi possível adicionar a live agora."; }
            return null;
        }

        private static string HexUtf8(string valor)
        {
            return BitConverter.ToString(Encoding.UTF8.GetBytes(valor)).Replace("-", "").ToLowerInvariant();
        }

        private static void ExecutarSessaoThread(string vpy, string argumentos)
        {
            try
            {
                string pathExtra = Path.Combine(Raiz, @"ffmpeg\bin") + ";" + Path.Combine(Raiz, @".venv\Scripts");
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = vpy;
                psi.Arguments = argumentos;
                psi.WorkingDirectory = Raiz;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardInput = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;

                psi.EnvironmentVariables["PATH"] = GarantirPathSistema(pathExtra);
                psi.EnvironmentVariables["HF_HOME"] = Path.Combine(Raiz, "modelos");
                psi.EnvironmentVariables["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1";
                psi.EnvironmentVariables["PYTHONPATH"] = Raiz;
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                psi.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";

                Process p = new Process();
                p.StartInfo = psi;
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null) ProcessarLinhaSessao(e.Data);
                };
                p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrWhiteSpace(e.Data)) ProcessarLinhaSessao(e.Data);
                };

                lock (LockSessao) { ProcSessao = p; }
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                p.WaitForExit();

                int code = p.ExitCode;
                lock (LockSessao)
                {
                    ProcSessao = null;
                    SessaoRodando = false;
                    if (code != 0 && string.IsNullOrEmpty(SessaoErro) && SessaoEstado != "encerrando")
                    {
                        SessaoErro = "A sessão encerrou com erro. Confira se o link da live está correto e online.";
                    }
                    SessaoEstado = "parado";
                    if (string.IsNullOrEmpty(SessaoErro))
                    {
                        SessaoUltimaMsg = "Sessão finalizada. Seus cortes estão prontos abaixo.";
                    }
                }
            }
            catch (Exception ex)
            {
                lock (LockSessao)
                {
                    ProcSessao = null;
                    SessaoRodando = false;
                    SessaoEstado = "parado";
                    SessaoErro = "Falha ao iniciar o motor de corte: " + ex.Message;
                }
            }
        }

        private static void ProcessarLinhaSessao(string linha)
        {
            int marca = linha.IndexOf("@@STATUS@@", StringComparison.Ordinal);
            if (marca > 0)
            {
                // Linha de log colada ao status: trata cada parte separada e nunca mostra o JSON cru.
                ProcessarLinhaSessao(linha.Substring(0, marca));
                ProcessarLinhaSessao(linha.Substring(marca));
                return;
            }
            if (marca == 0)
            {
                string json = linha.Substring(10);
                try
                {
                    JavaScriptSerializer js = new JavaScriptSerializer();
                    Dictionary<string, object> st = js.Deserialize<Dictionary<string, object>>(json);
                    if (st == null) return;
                    lock (LockSessao)
                    {
                        if (st.ContainsKey("estado") && st["estado"] != null)
                            SessaoEstado = Convert.ToString(st["estado"]);
                        if (st.ContainsKey("mensagem") && st["mensagem"] != null)
                            SessaoUltimaMsg = Convert.ToString(st["mensagem"]);
                        if (st.ContainsKey("erro") && st["erro"] != null)
                            SessaoErro = Convert.ToString(st["erro"]);
                        if (st.ContainsKey("modo") && st["modo"] != null)
                            SessaoDetalheServicos = Convert.ToString(st["modo"]);
                        if (st.ContainsKey("atraso_canal"))
                            SessaoAtrasoCanal = st["atraso_canal"] != null ? Convert.ToString(st["atraso_canal"]) : null;
                        if (st.ContainsKey("ultimo_corte_ha_s"))
                            SessaoUltimoCorteHaS = st["ultimo_corte_ha_s"] != null ? (double?)Convert.ToDouble(st["ultimo_corte_ha_s"]) : null;

                        if (st.ContainsKey("lives") && st["lives"] is System.Collections.ArrayList)
                        {
                            System.Collections.ArrayList arr = (System.Collections.ArrayList)st["lives"];
                            int somaCortes = 0;
                            double maiorLiveS = 0;
                            double? atraso = null;
                            List<string> canais = new List<string>();
                            foreach (object o in arr)
                            {
                                Dictionary<string, object> lv = o as Dictionary<string, object>;
                                if (lv == null) continue;
                                if (lv.ContainsKey("canal") && lv["canal"] != null)
                                    canais.Add(Convert.ToString(lv["canal"]));
                                if (lv.ContainsKey("cortes") && lv["cortes"] != null)
                                    somaCortes += Convert.ToInt32(lv["cortes"]);
                                if (lv.ContainsKey("live_s") && lv["live_s"] != null)
                                {
                                    double ls = Convert.ToDouble(lv["live_s"]);
                                    if (ls > maiorLiveS) maiorLiveS = ls;
                                }
                                string estLv = lv.ContainsKey("estado") && lv["estado"] != null ? Convert.ToString(lv["estado"]) : "";
                                bool ativaLv = (estLv == "ao vivo" || estLv == "gravando") &&
                                               (!lv.ContainsKey("pode_parar") || !(lv["pode_parar"] is bool) || (bool)lv["pode_parar"]);
                                if (ativaLv && lv.ContainsKey("atraso_transcricao_s") && lv["atraso_transcricao_s"] != null)
                                {
                                    double atrLv = Convert.ToDouble(lv["atraso_transcricao_s"]);
                                    if (!atraso.HasValue || atrLv > atraso.Value) atraso = atrLv;
                                }
                            }
                            if (st.ContainsKey("atraso_s") && st["atraso_s"] != null)
                            {
                                atraso = Convert.ToDouble(st["atraso_s"]);
                            }
                            SessaoLives.Clear();
                            foreach (object o2 in arr)
                            {
                                Dictionary<string, object> lv2 = o2 as Dictionary<string, object>;
                                if (lv2 == null) continue;
                                Dictionary<string, object> item = new Dictionary<string, object>();
                                item["id"] = lv2.ContainsKey("id") ? Convert.ToString(lv2["id"]) : "";
                                item["canal"] = lv2.ContainsKey("canal") && lv2["canal"] != null ? Convert.ToString(lv2["canal"]) : "";
                                item["estado"] = lv2.ContainsKey("estado") && lv2["estado"] != null ? Convert.ToString(lv2["estado"]) : "";
                                item["cortes"] = lv2.ContainsKey("cortes") && lv2["cortes"] != null ? Convert.ToInt32(lv2["cortes"]) : 0;
                                item["pode_parar"] = lv2.ContainsKey("pode_parar") && lv2["pode_parar"] is bool && (bool)lv2["pode_parar"];
                                item["video_gravado"] = ValorBool(lv2, "video_gravado");
                                item["live_s"] = lv2.ContainsKey("live_s") && lv2["live_s"] != null ? Convert.ToDouble(lv2["live_s"]) : 0.0;
                                item["atraso_transcricao_s"] = lv2.ContainsKey("atraso_transcricao_s") && lv2["atraso_transcricao_s"] != null
                                    ? (object)Convert.ToDouble(lv2["atraso_transcricao_s"]) : null;
                                item["atividade"] = lv2.ContainsKey("atividade") && lv2["atividade"] != null ? Convert.ToString(lv2["atividade"]) : "";
                                item["aviso"] = lv2.ContainsKey("aviso") && lv2["aviso"] != null ? Convert.ToString(lv2["aviso"]) : null;
                                SessaoLives.Add(item);
                            }
                            SessaoCortes = somaCortes;
                            SessaoLiveS = maiorLiveS;
                            SessaoAtrasoS = atraso;
                            if (canais.Count > 0) SessaoCanal = string.Join(", ", canais.ToArray());
                        }
                    }
                }
                catch { }
                return;
            }

            string limpa = linha.Trim();
            if (string.IsNullOrEmpty(limpa)) return;
            lock (LockSessao)
            {
                SessaoLinhasLog.Add(limpa);
                if (SessaoLinhasLog.Count > 200) SessaoLinhasLog.RemoveAt(0);
                SessaoUltimaMsg = limpa;
                if (limpa.StartsWith("ERRO:", StringComparison.OrdinalIgnoreCase))
                {
                    SessaoErro = limpa.Substring(5).Trim();
                }
            }
        }

        private static string PararLiveInterno(string id)
        {
            id = (id ?? "").Trim().ToLowerInvariant();
            if (id.Length != 32) return "Live não encontrada.";
            foreach (char c in id)
            {
                if (!Uri.IsHexDigit(c)) return "Live não encontrada.";
            }
            Process p = null;
            lock (LockSessao) { p = ProcSessao; }
            if (p == null) return "Nenhuma live em andamento.";
            try
            {
                p.StandardInput.WriteLine("parar-live " + id);
                p.StandardInput.Flush();
            }
            catch { return "Não foi possível parar essa live agora."; }
            lock (LockSessao)
            {
                foreach (Dictionary<string, object> it in SessaoLives)
                {
                    if (Convert.ToString(it["id"]) == id)
                    {
                        it["estado"] = "encerrando";
                        it["pode_parar"] = false;
                    }
                }
            }
            return null;
        }

        private static void PararSessaoInterno(bool imediato)
        {
            Process p = null;
            lock (LockSessao)
            {
                p = ProcSessao;
                if (p != null && SessaoRodando)
                {
                    SessaoEstado = "encerrando";
                    SessaoUltimaMsg = "Finalizando cortes em andamento…";
                }
            }
            if (p == null) return;

            try
            {
                p.StandardInput.WriteLine("parar");
                p.StandardInput.Flush();
            }
            catch { }

            if (imediato)
            {
                if (!p.WaitForExit(3000)) MatarArvoreProcesso(p.Id);
                return;
            }

            ThreadPool.QueueUserWorkItem(delegate
            {
                try
                {
                    if (!p.WaitForExit(30000)) MatarArvoreProcesso(p.Id);
                }
                catch { }
            });
        }

        private static void MatarArvoreProcesso(int pid)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo("taskkill.exe", String.Format("/PID {0} /T /F", pid));
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process tk = Process.Start(psi))
                {
                    tk.WaitForExit(5000);
                }
            }
            catch { }
        }

        // =========================================================================
        // Abertura de vídeos, pastas e seletor de arquivo
        // =========================================================================
        private static string CaminhoDoCorte(string caminho)
        {
            if (string.IsNullOrWhiteSpace(caminho)) return null;
            string completo = Path.GetFullPath(caminho);
            string pasta = Path.GetFullPath(Path.Combine(Raiz, "cortes")) + Path.DirectorySeparatorChar;
            if (!Path.GetExtension(completo).Equals(".mp4", StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(completo)) return null;
            if (completo.StartsWith(pasta, StringComparison.OrdinalIgnoreCase)) return completo;

            // A configuração do motor pode apontar para uma pasta de saída diferente.
            System.Collections.IList cortes = ListarCortes() as System.Collections.IList;
            if (cortes == null) return null;
            foreach (object corte in cortes)
            {
                if (string.Equals(ValorStr(corte as Dictionary<string, object>, "caminho"), completo,
                    StringComparison.OrdinalIgnoreCase)) return completo;
            }
            return null;
        }

        private static bool AbrirNoShell(ProcessStartInfo destino)
        {
            // Associações de arquivo do Windows podem depender de COM em uma thread STA.
            bool abriu = false;
            Thread shell = new Thread(delegate()
            {
                try
                {
                    using (Process processo = Process.Start(destino)) { }
                    abriu = true;
                }
                catch (System.ComponentModel.Win32Exception) { }
                catch (InvalidOperationException) { }
            });
            shell.SetApartmentState(ApartmentState.STA);
            shell.IsBackground = true;
            shell.Start();
            return shell.Join(10000) && abriu;
        }

        private static string AbrirVideoCorte(string caminho)
        {
            try
            {
                string video = CaminhoDoCorte(caminho);
                if (video == null) return "O arquivo do corte não foi encontrado.";
                if (AbrirNoShell(new ProcessStartInfo(video)
                {
                    WorkingDirectory = Path.GetDirectoryName(video),
                    UseShellExecute = true,
                    Verb = "open",
                    WindowStyle = ProcessWindowStyle.Normal
                })) return null;
            }
            catch (ArgumentException) { }
            catch (IOException) { }
            return "O Windows não conseguiu abrir o MP4. Confira o reprodutor padrão.";
        }

        private static string AbrirPastaCortes(string caminhoArquivo)
        {
            try
            {
                string pasta = Path.GetFullPath(Path.Combine(Raiz, "cortes"));
                string argumentos;
                if (string.IsNullOrWhiteSpace(caminhoArquivo))
                {
                    Directory.CreateDirectory(pasta);
                    argumentos = EscapeArg(pasta);
                }
                else
                {
                    string video = CaminhoDoCorte(caminhoArquivo);
                    if (video == null) return "O arquivo do corte não foi encontrado.";
                    argumentos = "/select," + EscapeArg(video);
                }
                if (AbrirNoShell(new ProcessStartInfo("explorer.exe", argumentos)
                {
                    UseShellExecute = true,
                    WindowStyle = ProcessWindowStyle.Normal
                })) return null;
            }
            catch (ArgumentException) { }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return "O Windows não conseguiu abrir a pasta de cortes.";
        }

        private static string EscolherVideoDialogo()
        {
            if (Headless) return "";
            string selecionado = "";
            Thread sta = new Thread(delegate()
            {
                try
                {
                    using (OpenFileDialog dlg = new OpenFileDialog())
                    {
                        dlg.Title = "Escolher vídeo do computador para testar o CorteLive";
                        dlg.Filter = "Vídeos (*.mp4;*.mkv;*.mov;*.webm)|*.mp4;*.mkv;*.mov;*.webm|Todos os arquivos (*.*)|*.*";
                        string tv = Path.Combine(Raiz, "teste_video");
                        if (Directory.Exists(tv)) dlg.InitialDirectory = tv;
                        if (dlg.ShowDialog() == DialogResult.OK) selecionado = dlg.FileName;
                    }
                }
                catch { }
            });
            sta.SetApartmentState(ApartmentState.STA);
            sta.Start();
            sta.Join();
            return selecionado;
        }

        private static string EscolherPastaDialogo()
        {
            if (Headless) return "";
            string selecionado = "";
            Thread sta = new Thread(delegate()
            {
                try
                {
                    using (FolderBrowserDialog dlg = new FolderBrowserDialog())
                    {
                        dlg.Description = "Escolher a pasta para os cortes desta faixa de nota";
                        dlg.ShowNewFolderButton = true;
                        if (dlg.ShowDialog() == DialogResult.OK) selecionado = dlg.SelectedPath;
                    }
                }
                catch { }
            });
            sta.SetApartmentState(ApartmentState.STA);
            sta.Start();
            sta.Join();
            return selecionado;
        }

        // =========================================================================
        // Atualização via instalador\atualizar.ps1 (CONTRATO_ATUALIZACAO.md)
        // =========================================================================
        private static Dictionary<string, object> RespostaAtualizador(string etapa, string msg, string erro, int? exitCode)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            d["etapa"] = etapa;
            d["pct"] = 100;
            d["msg"] = msg;
            d["versao_atual"] = LerVersao();
            d["versao_nova"] = null;
            d["erro"] = erro;
            if (exitCode.HasValue) d["exit_code"] = exitCode.Value;
            return d;
        }

        // Versão com que ESTE .exe foi compilado (build_apps.ps1 grava AssemblyVersion = VERSION).
        private static string VersaoExe()
        {
            try
            {
                Version v = Assembly.GetExecutingAssembly().GetName().Version;
                return String.Format("{0}.{1}.{2}", v.Major, v.Minor, v.Build);
            }
            catch { return ""; }
        }

        private static Dictionary<string, object> BlocoAtualizacao()
        {
            lock (LockAtual)
            {
                if (AtualUltimo == null) return null;
                Dictionary<string, object> d = new Dictionary<string, object>(AtualUltimo);
                d["rodando"] = AtualRodando;
                return d;
            }
        }

        // Clique em "Atualizar": roda o atualizador em segundo plano e devolve na hora; o painel
        // mostra o progresso. No fim, o --self-test do .exe novo deixa o auxiliar de reabertura
        // (AgendarReaberturaSeAtualizando), que fecha este app e abre a versão nova.
        private static object IniciarAtualizacaoEmSegundoPlano(string flagModo, int timeoutMs)
        {
            lock (LockAtual)
            {
                if (AtualRodando) return BlocoAtualizacao();
                AtualRodando = true;
                Dictionary<string, object> ini = new Dictionary<string, object>();
                ini["etapa"] = "checando";
                ini["pct"] = 2;
                ini["msg"] = "Procurando a versão nova...";
                ini["versao_atual"] = LerVersao();
                AtualUltimo = ini;
            }
            Thread th = new Thread(delegate()
            {
                Dictionary<string, object> fim;
                try
                {
                    fim = ExecutarAtualizador(flagModo, timeoutMs, delegate(Dictionary<string, object> ev)
                    {
                        lock (LockAtual) { AtualUltimo = ev; }
                    });
                }
                catch (Exception ex)
                {
                    fim = RespostaAtualizador("erro", "Não foi possível atualizar agora.", ex.Message, 1);
                }
                lock (LockAtual)
                {
                    AtualUltimo = fim;
                    AtualRodando = false;
                }
            });
            th.IsBackground = true;
            th.Start();
            return BlocoAtualizacao();
        }

        private static void RegistrarLogAtualizacao(string texto)
        {
            try
            {
                File.AppendAllText(Path.Combine(Raiz, "atualizacao.log"),
                    String.Format("[{0:s}] {1}\r\n", DateTime.Now, texto), Encoding.UTF8);
            }
            catch { }
        }

        private static Dictionary<string, object> LerJsonUrl(string url, string metodo, int timeoutMs)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = metodo;
            req.Timeout = timeoutMs;
            req.ReadWriteTimeout = timeoutMs;
            if (metodo == "POST")
            {
                req.ContentType = "application/json";
                byte[] corpo = Encoding.UTF8.GetBytes("{}");
                req.ContentLength = corpo.Length;
                using (Stream s = req.GetRequestStream()) s.Write(corpo, 0, corpo.Length);
            }
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (StreamReader sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(sr.ReadToEnd());
            }
        }

        private static void AgendarReaberturaSeAtualizando()
        {
            try
            {
                if (Environment.GetEnvironmentVariable("CORTELIVE_SEM_REABRIR") == "1") return;
                if (!Directory.Exists(Path.Combine(Raiz, "_update_tmp"))) return;
                string arq = Path.Combine(Raiz, ArqInstanciaNome);
                if (!File.Exists(arq)) return;
                Dictionary<string, object> inst = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(arq, Encoding.UTF8));
                if (inst == null || !inst.ContainsKey("porta")) return;
                int porta = Convert.ToInt32(inst["porta"]);
                int pid = inst.ContainsKey("pid") ? Convert.ToInt32(inst["pid"]) : 0;
                Dictionary<string, object> ping = LerJsonUrl(String.Format("http://127.0.0.1:{0}/api/ping", porta), "GET", 1500);
                string exeAberto = ping != null && ping.ContainsKey("exe_versao") ? Convert.ToString(ping["exe_versao"]) : "";
                string versaoDisco = LerVersao();
                if (exeAberto == versaoDisco) return;
                ProcessStartInfo psi = new ProcessStartInfo(Application.ExecutablePath, String.Format(
                    "--reabrir-apos-atualizar --raiz \"{0}\" --porta-antiga {1} --pid-antigo {2} --versao-nova {3}",
                    Raiz, porta, pid, versaoDisco));
                // ShellExecute: o auxiliar não herda a saída do atualizador (que espera o self-test acabar).
                psi.UseShellExecute = true;
                psi.WorkingDirectory = Raiz;
                Process.Start(psi);
                RegistrarLogAtualizacao(String.Format("REABRIR agendado: app aberto v{0} (pid {1}, porta {2}) -> v{3}",
                    exeAberto == "" ? "antiga" : exeAberto, pid, porta, versaoDisco));
            }
            catch (Exception ex)
            {
                RegistrarLogAtualizacao("REABRIR não agendado: " + ex.Message);
            }
        }

        private static void FecharJanelaDaPorta(int porta)
        {
            if (porta <= 0) return;
            // Só a janela do painel desta instância (perfil CorteLive\webview_* com --app na porta antiga).
            string ps = "$ErrorActionPreference='SilentlyContinue'; " +
                "foreach ($p in (Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\")) { " +
                "$c = [string]$p.CommandLine; " +
                "if ($c -like '*CorteLive\\webview_*' -and $c -like '*--app=http://127.0.0.1:" + porta + "/*') { Stop-Process -Id $p.ProcessId -Force } }";
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(CaminhoPowerShell(),
                    "-NoProfile -ExecutionPolicy Bypass -EncodedCommand " + Convert.ToBase64String(Encoding.Unicode.GetBytes(ps)));
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi)) p.WaitForExit(20000);
            }
            catch { }
        }

        // Processo auxiliar: espera o atualizador terminar, fecha o app antigo (e a janela dele)
        // e abre o CorteLive novo. Se a atualização foi desfeita, não faz nada.
        private static int ExecutarReabertura()
        {
            bool criado;
            using (Mutex mx = new Mutex(true, "Local\\CorteLiveReabrir_" + (Raiz.ToLowerInvariant().GetHashCode() & 0x7fffffff), out criado))
            {
                if (!criado) return 0;
                string tmp = Path.Combine(Raiz, "_update_tmp");
                DateTime limite = DateTime.UtcNow.AddMinutes(30);
                while (Directory.Exists(tmp) && DateTime.UtcNow < limite) Thread.Sleep(500);
                Thread.Sleep(2500);

                string versaoDisco = LerVersao();
                if (!string.IsNullOrEmpty(ReabrirVersao) && versaoDisco != ReabrirVersao)
                {
                    RegistrarLogAtualizacao(String.Format("REABRIR cancelado: versão em disco {0} (esperada {1}); atualização desfeita.", versaoDisco, ReabrirVersao));
                    return 0;
                }

                if (ReabrirPortaAntiga > 0)
                {
                    try { LerJsonUrl(String.Format("http://127.0.0.1:{0}/api/encerrar", ReabrirPortaAntiga), "POST", 3000); } catch { }
                }
                if (ReabrirPidAntigo > 0 && ReabrirPidAntigo != Process.GetCurrentProcess().Id)
                {
                    try
                    {
                        using (Process antigo = Process.GetProcessById(ReabrirPidAntigo))
                        {
                            if (!antigo.WaitForExit(20000))
                            {
                                MatarArvoreProcesso(antigo.Id);
                                antigo.WaitForExit(5000);
                            }
                        }
                    }
                    catch { }
                }
                FecharJanelaDaPorta(ReabrirPortaAntiga);
                try { File.Delete(Path.Combine(Raiz, ArqInstanciaNome)); } catch { }

                string extra = (Environment.GetEnvironmentVariable("CORTELIVE_REABRIR_ARGS") ?? "").Trim();
                ProcessStartInfo psi = new ProcessStartInfo(Path.Combine(Raiz, "CorteLive.exe"),
                    ("--atualizado " + versaoDisco + " " + extra).Trim());
                psi.UseShellExecute = true;
                psi.WorkingDirectory = Raiz;
                Process novo = Process.Start(psi);
                RegistrarLogAtualizacao(String.Format("REABRIR: app antigo (pid {0}) fechado; CorteLive v{1} aberto (pid {2}).",
                    ReabrirPidAntigo, versaoDisco, novo != null ? novo.Id.ToString() : "?"));
            }
            return 0;
        }

        private static Dictionary<string, object> ExecutarAtualizador(string flagModo, int timeoutMs)
        {
            return ExecutarAtualizador(flagModo, timeoutMs, null);
        }

        private static Dictionary<string, object> ExecutarAtualizador(string flagModo, int timeoutMs, Action<Dictionary<string, object>> aoEvento)
        {
            string script = Path.Combine(Raiz, @"instalador\atualizar.ps1");
            if (!File.Exists(script))
            {
                return RespostaAtualizador("indisponivel", "Script instalador\\atualizar.ps1 ainda não instalado.", null, null);
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = CaminhoPowerShell();
            psi.Arguments = String.Format("-NoProfile -ExecutionPolicy Bypass -File \"{0}\" {1} -Raiz \"{2}\"", script, flagModo, Raiz);
            psi.WorkingDirectory = Raiz;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PATH"] = GarantirPathSistema("");

            using (Process p = Process.Start(psi))
            {
                StringBuilder outSb = new StringBuilder();
                JavaScriptSerializer jsEv = new JavaScriptSerializer();
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data == null) return;
                    lock (outSb) outSb.AppendLine(e.Data);
                    if (aoEvento == null) return;
                    string l = e.Data.Trim();
                    if (!l.StartsWith("{") || !l.EndsWith("}")) return;
                    try
                    {
                        Dictionary<string, object> ev = jsEv.Deserialize<Dictionary<string, object>>(l);
                        if (ev != null) aoEvento(ev);
                    }
                    catch { }
                };
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                if (!p.WaitForExit(timeoutMs))
                {
                    MatarArvoreProcesso(p.Id);
                    return RespostaAtualizador("erro", "Tempo esgotado.", "Tempo esgotado.", null);
                }
                p.WaitForExit();

                int exitCode = p.ExitCode;
                string saida;
                lock (outSb) saida = outSb.ToString();
                string[] linhas = saida.Split(new char[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
                JavaScriptSerializer js = new JavaScriptSerializer();
                for (int i = linhas.Length - 1; i >= 0; i--)
                {
                    string l = linhas[i].Trim();
                    if (l.StartsWith("{") && l.EndsWith("}"))
                    {
                        try
                        {
                            Dictionary<string, object> d = js.Deserialize<Dictionary<string, object>>(l);
                            if (d != null)
                            {
                                d["exit_code"] = exitCode;
                                return d;
                            }
                        }
                        catch { }
                    }
                }
                return RespostaAtualizador("erro", "Não foi possível atualizar agora.", "Resposta inesperada do atualizador.", exitCode);
            }
        }

    }
}
