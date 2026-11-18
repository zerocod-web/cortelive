using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Instalador do CorteLive")]
[assembly: AssemblyProduct("CorteLive")]
[assembly: AssemblyCompany("zerocod")]

namespace CorteLiveSetup
{
    internal static class Setup
    {
        private const string Api = "https://api.github.com/repos/zerocod-web/cortelive/releases/latest";
        private const string ZipName = "cortelive_amigos.zip";
        private const string AppName = "CorteLive.exe";
        private const string Uninstaller = "CorteLive-Desinstalar.exe";
        private const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\";
        private static readonly string Local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        internal static readonly string DefaultFolder = Path.Combine(Local, "CorteLive");
        private const string LogName = "setup_log.txt";
        // Pasta padrão até a instalação aceitar a pasta escolhida; daí em diante o log vai para ela.
        private static volatile string logFolder = DefaultFolder;
        internal static string LogPath { get { return Path.Combine(logFolder, LogName); } }
        private static readonly string AppPath = Application.ExecutablePath;
        private static readonly StringComparison IgnoreCase = StringComparison.OrdinalIgnoreCase;

        // Mesmas pastas e arquivos de usuário que o atualizador preserva na raiz.
        private static readonly HashSet<string> ProtectedFolders = new HashSet<string>(new[] {
            ".venv", "venv", "python", "ffmpeg", "ferramentas", "modelos", "cortes",
            "versoes_antigas", "_downloads", "_update_tmp", ".buffer", "logs", "dist",
            ".git", ".github", ".githooks", ".agents", ".codex", ".gemini", ".overclock-app",
            "qa", "testes", "teste_video", "build", ".pytest_cache", "contas", "secrets",
            "tokens", "cookies", "perfil_navegador", "perfil-browser"
        }, StringComparer.OrdinalIgnoreCase);
        private static readonly HashSet<string> ProtectedFiles = new HashSet<string>(new[] {
            "config.toml", "postar.toml", "usuario.toml", "lives.txt", "painel_config.json",
            "instalacao_log.txt", "atualizacao.log", "setup_log.txt", ".gitignore"
        }, StringComparer.OrdinalIgnoreCase);
        private static readonly string[] InternalSettings = {
            @"instalador\modo.txt", @"instalador\cache_release.local.json",
            @"cortelive\postar\config.toml", @"painel\config.local.json"
        };
        private static readonly HashSet<string> SharedProfile = new HashSet<string>(new[] {
            "setup_log.txt", "cams.json", "encoder.json", "contas", "navegador",
            "perfil-browser", "webview_app", "webview_dev", "webview_ao_vivo"
        }, StringComparer.OrdinalIgnoreCase);

        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                if (args.Length == 1 && args[0] == "--desinstalar")
                {
                    StartUninstaller();
                    return 0;
                }
                if (args.Length == 4 && args[0] == "--remover")
                {
                    try
                    {
                        Process parent = null;
                        try { parent = Process.GetProcessById(int.Parse(args[3])); }
                        catch (ArgumentException) { } // O iniciador já terminou.
                        using (parent)
                            if (parent != null && !parent.WaitForExit(10000))
                                throw new IOException("O desinstalador anterior ainda está aberto. Tente novamente.");
                        return Uninstall(args[1], args[2]);
                    }
                    finally { RemoveTemporaryUninstaller(); }
                }
                // Modo de teste automatizado: só vale com a API de teste local (CORTELIVE_RELEASE_API_URL).
                if (args.Length == 3 && args[0] == "--silencioso" && args[1] == "--pasta"
                    && !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("CORTELIVE_RELEASE_API_URL")))
                {
                    Install(args[2], delegate(int pct, string msg) { Console.Error.WriteLine(pct + "% " + msg); });
                    return 0;
                }
                Application.Run(new SetupWindow());
                return 0;
            }
            catch (Exception ex)
            {
                Log(ex.ToString());
                MessageBox.Show(ex.Message, "CorteLive", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        internal static void Install(string input, Action<int, string> progress)
        {
            string target = ValidatePath(input);
            WithInstallLock(target, delegate
            {
                bool existing = IsPanelPackage(target) || IsLegacyInstallation(target);
                if (Directory.Exists(target) && !existing &&
                    Directory.EnumerateFileSystemEntries(target).Any(p =>
                    {
                        string name = Path.GetFileName(p);
                        // O log de uma tentativa anterior nesta pasta não impede tentar de novo.
                        return !name.Equals(LogName, IgnoreCase) &&
                            (!target.Equals(DefaultFolder, IgnoreCase) || !SharedProfile.Contains(name));
                    }))
                    throw new InvalidOperationException("Escolha uma pasta vazia ou uma instalação existente do CorteLive.");
                EnsureAppClosed(target);

                logFolder = target;
                Log("Instalação iniciada.");
                progress(5, "Buscando a versão mais recente…");
                string overrideUrl = Environment.GetEnvironmentVariable("CORTELIVE_RELEASE_API_URL");
                Uri endpoint = new Uri(string.IsNullOrWhiteSpace(overrideUrl) ? Api : overrideUrl.Trim());
                bool test = !string.IsNullOrWhiteSpace(overrideUrl);
                if (test && (!endpoint.IsLoopback || endpoint.Scheme != Uri.UriSchemeHttp))
                    throw new InvalidOperationException("A API de teste precisa usar HTTP e apontar para este computador.");
                ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;

                // Mesma unidade do destino para que a troca e a restauração usem renomeação.
                string parent = Path.GetDirectoryName(target);
                Directory.CreateDirectory(parent);
                string work = Path.Combine(parent, ".cortelive-setup-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(work);
                bool keepBackup = false;
                try
                {
                    string zip = Path.Combine(work, ZipName);
                    Release release;
                    using (var web = new ReleaseClient(test))
                    {
                        release = new JavaScriptSerializer().Deserialize<Release>(web.DownloadString(endpoint));
                        if (release == null || string.IsNullOrWhiteSpace(release.tag_name) || release.draft || release.prerelease)
                            throw new InvalidOperationException("A lista do GitHub não contém uma versão estável.");
                        Uri zipUrl = AssetUrl(release, ZipName, endpoint, test);
                        Uri shaUrl = AssetUrl(release, ZipName + ".sha256", endpoint, test);
                        progress(10, "Baixando o CorteLive " + release.tag_name + "…");
                        web.DownloadPackage(zipUrl, zip, progress);
                        progress(70, "Conferindo o SHA-256 do pacote…");
                        string sha = Regex.Match(web.DownloadString(shaUrl).Trim(), @"\A[0-9a-fA-F]{64}(?=\s|\z)").Value;
                        using (var file = File.OpenRead(zip))
                            if (sha.Length == 0 || !Hash(file).Equals(sha, IgnoreCase))
                                throw new InvalidDataException("A verificação SHA-256 falhou. Baixe o pacote novamente.");
                        Log("SHA-256 conferido.");
                    }

                    progress(80, "Extraindo os arquivos do CorteLive…");
                    string payload = Path.Combine(work, "pacote");
                    ZipFile.ExtractToDirectory(zip, payload);
                    if (!IsPanelPackage(payload))
                    {
                        string[] items = Directory.GetFileSystemEntries(payload);
                        if (items.Length == 1 && Directory.Exists(items[0])) payload = items[0];
                    }
                    if (!IsPanelPackage(payload) || !File.Exists(Path.Combine(payload, "VERSION")))
                        throw new InvalidDataException("O pacote está incompleto. Tente de novo quando houver uma nova versão no GitHub.");
                    string version = File.ReadAllText(Path.Combine(payload, "VERSION")).Trim();
                    if (version.Length == 0 || !version.Equals(release.tag_name.Trim().TrimStart('v', 'V'), IgnoreCase))
                        throw new InvalidDataException("A versão do pacote está diferente da versão anunciada no GitHub.");
                    string localVersion = Path.Combine(target, "VERSION");
                    Version installed, downloaded;
                    if (existing && File.Exists(localVersion) && Version.TryParse(File.ReadAllText(localVersion).Trim(), out installed)
                        && Version.TryParse(version, out downloaded) && installed > downloaded)
                        throw new InvalidOperationException("Esta pasta já tem uma versão mais recente do CorteLive.");

                    File.Copy(AppPath, Path.Combine(payload, Uninstaller), true);
                    progress(88, existing ? "Atualizando o painel e mantendo seus arquivos…" : "Instalando o painel…");
                    EnsureAppClosed(target);
                    InstallFiles(payload, target, Path.Combine(work, "anterior"), existing, ref keepBackup);

                    progress(96, "Criando os atalhos e abrindo o painel…");
                    RegisterInstallation(target, version);
                    using (Process app = Process.Start(new ProcessStartInfo(Path.Combine(target, AppName), test ? "--headless" : "") {
                        WorkingDirectory = target, UseShellExecute = false
                    }))
                        if (app == null) throw new InvalidOperationException("O Windows não conseguiu abrir o CorteLive.");
                    Log("Instalação concluída. CorteLive aberto.");
                    progress(100, "Painel aberto. Continue a instalação por lá.");
                }
                finally
                {
                    if (!keepBackup) TryDelete(work);
                    else Log("Backup da instalação anterior retido em " + work);
                }
            });
        }

        private static void InstallFiles(string payload, string target, string backup, bool existing, ref bool keepBackup)
        {
            Directory.CreateDirectory(target);
            Directory.CreateDirectory(backup);
            var installed = new List<string>();
            var saved = new List<string>();
            try
            {
                foreach (string path in Directory.GetFileSystemEntries(payload))
                {
                    string name = Path.GetFileName(path);
                    string destination = Path.Combine(target, name);
                    bool folder = Directory.Exists(path);
                    if (folder ? IsProtectedFolder(name) : existing && ProtectedFile(name)) continue;
                    if (Exists(destination))
                    {
                        RejectReparsePoint(destination);
                        if (folder && Directory.Exists(destination)) PreserveSettings(name, destination, path);
                        Move(destination, Path.Combine(backup, name));
                        saved.Add(name);
                    }
                    Move(path, destination);
                    installed.Add(name);
                }
                TestApp(target);
            }
            catch (Exception original)
            {
                try
                {
                    foreach (string name in installed) DeleteTree(Path.Combine(target, name));
                    foreach (string name in saved) Move(Path.Combine(backup, name), Path.Combine(target, name));
                }
                catch (Exception rollback)
                {
                    keepBackup = true;
                    Log(original.ToString());
                    Log(rollback.ToString());
                    throw new IOException("A instalação foi interrompida. A versão anterior foi guardada em:\n" + backup, rollback);
                }
                throw;
            }
        }

        private static void PreserveSettings(string folder, string previous, string replacement)
        {
            foreach (string relative in InternalSettings)
            {
                if (!relative.StartsWith(folder + @"\", IgnoreCase)) continue;
                string tail = relative.Substring(folder.Length + 1);
                string oldFile = Path.Combine(previous, tail);
                if (!File.Exists(oldFile)) continue;
                ValidateAncestors(oldFile);
                string newFile = Path.Combine(replacement, tail);
                Directory.CreateDirectory(Path.GetDirectoryName(newFile));
                File.Copy(oldFile, newFile, true);
            }
        }

        private static bool IsProtectedFolder(string name)
        {
            return ProtectedFolders.Contains(name) || SharedProfile.Contains(name) || name.StartsWith("sessao_login", IgnoreCase)
                || name.EndsWith(".egg-info", IgnoreCase);
        }

        private static bool ProtectedFile(string name)
        {
            return ProtectedFiles.Contains(name) || SharedProfile.Contains(name) || Regex.IsMatch(name,
                @"\.local\.|^config\.local|^\.env|token|cookie|^client_secret|^credentials|^auth.*\.json$|\.(log|key|pem|pfx|p12)$",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        }

        private static void TestApp(string target)
        {
            string app = Path.Combine(target, AppName);
            using (Process test = Process.Start(new ProcessStartInfo(app, "--self-test") {
                WorkingDirectory = target, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden
            }))
            {
                if (!test.WaitForExit(15000))
                {
                    test.Kill();
                    test.WaitForExit(5000);
                    throw new InvalidOperationException("O painel demorou demais para responder durante a validação.");
                }
                if (test.ExitCode != 0)
                    throw new InvalidOperationException("O painel não passou no teste de abertura. Tente baixar a versão novamente.");
            }
        }

        private static void RegisterInstallation(string target, string version)
        {
            string app = Path.Combine(target, AppName);
            foreach (string shortcut in Shortcuts(target)) CreateShortcut(shortcut, app, target);
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(UninstallKey + InstallId(target)))
            {
                key.SetValue("DisplayName", "CorteLive");
                key.SetValue("DisplayVersion", version);
                key.SetValue("Publisher", "zerocod");
                key.SetValue("InstallLocation", target);
                key.SetValue("DisplayIcon", app + ",0");
                key.SetValue("UninstallString", "\"" + Path.Combine(target, Uninstaller) + "\" --desinstalar");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        }

        private static IEnumerable<string> Shortcuts(string target)
        {
            string name = target.Equals(DefaultFolder, IgnoreCase) ? "CorteLive" :
                "CorteLive - " + Path.GetFileName(target) + " (" + InstallId(target).Substring(10, 8) + ")";
            foreach (var folder in new[] { Environment.SpecialFolder.DesktopDirectory, Environment.SpecialFolder.Programs })
                yield return Path.Combine(Environment.GetFolderPath(folder, Environment.SpecialFolderOption.Create), name + ".lnk");
        }

        private static void CreateShortcut(string path, string app, string target)
        {
            dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
            dynamic shortcut = shell.CreateShortcut(path);
            try
            {
                shortcut.TargetPath = app;
                shortcut.WorkingDirectory = target;
                shortcut.IconLocation = app + ",0";
                shortcut.Description = "Abrir o painel CorteLive";
                shortcut.Save();
            }
            finally
            {
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shortcut);
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
            }
        }

        private static void StartUninstaller()
        {
            string target = ValidatePath(Path.GetDirectoryName(AppPath));
            string id = InstallId(target);
            CheckRegistration(target, id);
            string temporary = Path.Combine(Path.GetTempPath(), "CorteLive-remover-" + Guid.NewGuid().ToString("N") + ".exe");
            File.Copy(AppPath, temporary);
            using (Process parent = Process.GetCurrentProcess())
            using (Process child = Process.Start(new ProcessStartInfo(temporary,
                "--remover \"" + target + "\" " + id + " " + parent.Id) {
                    UseShellExecute = false, WorkingDirectory = Path.GetTempPath()
                })) { }
        }

        private static int Uninstall(string target, string id)
        {
            target = ValidatePath(target);
            WithInstallLock(target, delegate
            {
                CheckRegistration(target, id);
                EnsureAppClosed(target);
                string cuts = Path.Combine(target, "cortes");
                bool keep = false;
                if (Directory.Exists(cuts))
                {
                    DialogResult choice = MessageBox.Show("Apagar também os cortes salvos?\n\n" +
                        "Sim: apagar o CorteLive e os cortes.\nNão: guardar os cortes ao lado da pasta de instalação.",
                        "Desinstalar CorteLive", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
                    if (choice == DialogResult.Cancel) return;
                    keep = choice == DialogResult.No;
                    if (keep) RejectReparsePoint(cuts);
                }
                else if (MessageBox.Show("Desinstalar o CorteLive e remover seus atalhos?",
                    "Desinstalar CorteLive", MessageBoxButtons.OKCancel, MessageBoxIcon.Question,
                    MessageBoxDefaultButton.Button2) != DialogResult.OK) return;

                string saved = null;
                if (keep)
                {
                    string sibling = target + " - Cortes";
                    saved = sibling;
                    for (int i = 2; Exists(saved); i++) saved = sibling + " (" + i + ")";
                    Directory.Move(cuts, saved);
                }
                // Mantém o desinstalador até a limpeza terminar para possibilitar nova tentativa.
                foreach (string path in Directory.GetFileSystemEntries(target))
                    if (!Path.GetFileName(path).Equals(Uninstaller, IgnoreCase)) DeleteTree(path);
                foreach (string shortcut in Shortcuts(target)) File.Delete(shortcut);
                File.Delete(Path.Combine(target, Uninstaller));
                Directory.Delete(target);
                Registry.CurrentUser.DeleteSubKeyTree(UninstallKey + id, false);
                MessageBox.Show(saved == null ? "CorteLive desinstalado." :
                    "CorteLive desinstalado. Seus cortes ficaram em:\n" + saved,
                    "Desinstalar CorteLive", MessageBoxButtons.OK, MessageBoxIcon.Information);
            });
            return 0;
        }

        private static void CheckRegistration(string target, string id)
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey + InstallId(target)))
                if (id != InstallId(target) || key == null || !target.Equals(key.GetValue("InstallLocation") as string, IgnoreCase)
                    || !File.Exists(Path.Combine(target, Uninstaller)))
                    throw new InvalidOperationException("O Windows não encontrou uma instalação registrada do CorteLive nessa pasta.");
        }

        private static void RemoveTemporaryUninstaller()
        {
            string name = Path.GetFileName(AppPath);
            if (!Regex.IsMatch(name, @"\ACorteLive-remover-[0-9a-f]{32}\.exe\z") ||
                !Path.GetDirectoryName(AppPath).Equals(Path.GetTempPath().TrimEnd('\\'), IgnoreCase)) return;
            try
            {
                using (Process current = Process.GetCurrentProcess())
                {
                    string escapedPath = AppPath.Replace("'", "''");
                    string script = "$p=Get-Process -Id " + current.Id + " -ErrorAction SilentlyContinue; " +
                        "if ($p) { $p.WaitForExit(15000) | Out-Null }; Remove-Item -LiteralPath '" + escapedPath + "' -Force";
                    string encoded = Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
                    string powershell = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe");
                    using (Process cleanup = Process.Start(new ProcessStartInfo(powershell,
                        "-NoProfile -NonInteractive -EncodedCommand " + encoded) {
                        UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden
                    })) { }
                }
            }
            catch (Exception ex) { Log(ex.Message); }
        }

        private static List<Process> AppsAbertos(string target)
        {
            var abertos = new List<Process>();
            string exe = Path.Combine(target, AppName);
            foreach (Process process in Process.GetProcessesByName("CorteLive"))
            {
                string location = null;
                try { location = process.MainModule.FileName; }
                catch (InvalidOperationException) { }
                catch (System.ComponentModel.Win32Exception) { }
                if (location != null && location.Equals(exe, IgnoreCase)) abertos.Add(process);
                else process.Dispose();
            }
            return abertos;
        }

        // O painel aberto trava o próprio .exe e o perfil do Edge (webview_*). Em vez de pedir
        // para o amigo achar o ícone ao lado do relógio, o instalador pede para o painel sair
        // e, se ele não sair, encerra o painel e a janela dele.
        private static void EnsureAppClosed(string target)
        {
            if (!File.Exists(Path.Combine(target, AppName))) return;
            var abertos = AppsAbertos(target);
            if (abertos.Count == 0) { FecharJanelaDoPainel(target); return; }
            Log("CorteLive aberto em " + target + ": fechando antes de atualizar.");
            try
            {
                int porta = 0;
                string instancia = Path.Combine(target, "painel_instancia.json");
                if (File.Exists(instancia))
                {
                    var dados = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(instancia, Encoding.UTF8).TrimStart('\uFEFF'));
                    if (dados != null && dados.ContainsKey("porta")) porta = Convert.ToInt32(dados["porta"]);
                }
                if (porta > 0)
                {
                    var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + porta + "/api/encerrar");
                    req.Method = "POST"; req.ContentType = "application/json"; req.Timeout = 3000; req.ContentLength = 2;
                    using (Stream body = req.GetRequestStream()) body.Write(new byte[] { 123, 125 }, 0, 2);
                    using (req.GetResponse()) { }
                }
            }
            catch (Exception ex) { Log("Pedido de saída ao painel falhou: " + ex.Message); }
            foreach (Process process in abertos)
            {
                using (process)
                {
                    try
                    {
                        if (!process.WaitForExit(15000))
                        {
                            Log("O painel não saiu sozinho; encerrando o processo " + process.Id + ".");
                            using (Process tk = Process.Start(new ProcessStartInfo("taskkill.exe", "/PID " + process.Id + " /T /F") {
                                UseShellExecute = false, CreateNoWindow = true }))
                                tk.WaitForExit(10000);
                            process.WaitForExit(10000);
                        }
                    }
                    catch (InvalidOperationException) { }
                }
            }
            FecharJanelaDoPainel(target);
            var restantes = AppsAbertos(target);
            bool sobrou = restantes.Count > 0;
            foreach (Process process in restantes) process.Dispose();
            if (sobrou)
                throw new InvalidOperationException("Não consegui fechar o CorteLive. Feche o painel (botão direito no ícone ao lado do relógio, Sair) e tente de novo.");
        }

        // Fecha só o Edge do painel desta pasta (perfil <pasta>\webview_*); o Edge normal do amigo não é tocado.
        private static void FecharJanelaDoPainel(string target)
        {
            string ps = "$ErrorActionPreference='SilentlyContinue'; $pref = '" + target.Replace("'", "''") + "\\webview_'; " +
                "foreach ($p in (Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\")) { " +
                "if (([string]$p.CommandLine).IndexOf($pref, [StringComparison]::OrdinalIgnoreCase) -ge 0) { Stop-Process -Id $p.ProcessId -Force } }";
            try
            {
                string powershell = Path.Combine(Environment.SystemDirectory, @"WindowsPowerShell\v1.0\powershell.exe");
                using (Process p = Process.Start(new ProcessStartInfo(powershell,
                    "-NoProfile -ExecutionPolicy Bypass -EncodedCommand " + Convert.ToBase64String(Encoding.Unicode.GetBytes(ps))) {
                    UseShellExecute = false, CreateNoWindow = true }))
                    p.WaitForExit(20000);
            }
            catch (Exception ex) { Log("Não foi possível fechar a janela do painel: " + ex.Message); }
        }

        private static string ValidatePath(string input)
        {
            if (string.IsNullOrWhiteSpace(input) || !Regex.IsMatch(input.Trim(), @"\A[A-Za-z]:[\\/]"))
                throw new InvalidOperationException("Escolha uma pasta em um disco deste computador.");
            string path = Path.GetFullPath(input.Trim()).TrimEnd('\\');
            string parent = Path.GetDirectoryName(path);
            if (parent == null || path.Length <= 2 || path.Split('\\').Any(part => part.EndsWith(".") || part.EndsWith(" ")))
                throw new InvalidOperationException("Escolha uma pasta própria para o CorteLive.");
            foreach (var location in new[] {
                Environment.SpecialFolder.UserProfile, Environment.SpecialFolder.LocalApplicationData,
                Environment.SpecialFolder.ApplicationData, Environment.SpecialFolder.DesktopDirectory,
                Environment.SpecialFolder.MyDocuments, Environment.SpecialFolder.Windows,
                Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86,
                Environment.SpecialFolder.CommonApplicationData, Environment.SpecialFolder.Programs
            })
            {
                string system = Environment.GetFolderPath(location);
                if (system.Length != 0 && (path.Equals(system, IgnoreCase) ||
                    system.StartsWith(path + @"\", IgnoreCase)))
                    throw new InvalidOperationException("Escolha uma pasta própria para o CorteLive.");
            }
            ValidateAncestors(path);
            return path;
        }

        private static void ValidateAncestors(string path)
        {
            for (string part = path; part != null; part = Path.GetDirectoryName(part))
                if (Exists(part)) RejectReparsePoint(part);
        }

        private static void RejectReparsePoint(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("A pasta escolhida contém um redirecionamento do Windows. Escolha uma pasta local comum.");
        }

        private static bool IsPanelPackage(string folder)
        {
            return File.Exists(Path.Combine(folder, AppName))
                && File.Exists(Path.Combine(folder, @"instalador\instalar.ps1"))
                && File.Exists(Path.Combine(folder, @"cortelive\__init__.py"))
                && File.Exists(Path.Combine(folder, @"painel\index.html"));
        }

        private static bool IsLegacyInstallation(string folder)
        {
            return File.Exists(Path.Combine(folder, "INSTALAR_E_RODAR.bat"))
                && File.Exists(Path.Combine(folder, @"instalador\instalar.ps1"))
                && File.Exists(Path.Combine(folder, @"cortelive\__init__.py"));
        }

        private static bool Exists(string path) { return File.Exists(path) || Directory.Exists(path); }

        private static void Move(string source, string target)
        {
            if (Directory.Exists(source)) Directory.Move(source, target);
            else File.Move(source, target);
        }

        private static void DeleteTree(string path)
        {
            FileAttributes attributes = File.GetAttributes(path);
            if ((attributes & FileAttributes.Directory) != 0)
            {
                if ((attributes & FileAttributes.ReparsePoint) == 0)
                    foreach (string child in Directory.GetFileSystemEntries(path)) DeleteTree(child);
                Directory.Delete(path);
                return;
            }
            if ((attributes & FileAttributes.ReadOnly) != 0 && (attributes & FileAttributes.ReparsePoint) == 0)
                File.SetAttributes(path, attributes & ~FileAttributes.ReadOnly);
            File.Delete(path);
        }

        private static void TryDelete(string path)
        {
            try { if (Exists(path)) DeleteTree(path); }
            catch (Exception ex) { Log("Falha ao limpar arquivos temporários: " + ex.Message); }
        }

        private static string InstallId(string target)
        {
            using (var stream = new MemoryStream(Encoding.UTF8.GetBytes(target.ToUpperInvariant())))
                return "CorteLive-" + Hash(stream).Substring(0, 24);
        }

        private static string Hash(Stream input)
        {
            using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "");
        }

        private static void WithInstallLock(string target, Action action)
        {
            using (var mutex = new Mutex(false, @"Local\" + InstallId(target)))
            {
                bool locked;
                try { locked = mutex.WaitOne(0); }
                catch (AbandonedMutexException) { locked = true; }
                if (!locked) throw new InvalidOperationException("Já existe uma instalação ou desinstalação em andamento nessa pasta.");
                try { action(); }
                finally { mutex.ReleaseMutex(); }
            }
        }

        internal static void Log(string message)
        {
            try
            {
                string path = LogPath;
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.AppendAllText(path, DateTime.Now.ToString("s") + " " + message + Environment.NewLine);
            }
            catch (Exception ex) { Trace.WriteLine(ex.Message); }
        }

        private static Uri AssetUrl(Release release, string name, Uri endpoint, bool test)
        {
            Asset asset = (release.assets ?? new Asset[0]).FirstOrDefault(a => a != null && a.name == name);
            if (asset == null) throw new InvalidDataException("A versão " + release.tag_name + " ainda não oferece " + name + ".");
            Uri url;
            if (!Uri.TryCreate(asset.browser_download_url, UriKind.Absolute, out url) ||
                (test ? url.Scheme != endpoint.Scheme || url.Authority != endpoint.Authority :
                 url.Scheme != Uri.UriSchemeHttps || url.Host != "github.com" ||
                 !url.AbsolutePath.StartsWith("/zerocod-web/cortelive/releases/download/", IgnoreCase)))
                throw new InvalidDataException("O endereço do pacote publicado é inválido.");
            return url;
        }

        public sealed class Release
        {
            public string tag_name { get; set; }
            public bool draft { get; set; }
            public bool prerelease { get; set; }
            public Asset[] assets { get; set; }
        }

        public sealed class Asset
        {
            public string name { get; set; }
            public string browser_download_url { get; set; }
        }

        private sealed class ReleaseClient : WebClient
        {
            internal ReleaseClient(bool test)
            {
                Headers[HttpRequestHeader.UserAgent] = "CorteLive-Setup";
                Encoding = Encoding.UTF8;
                if (test) Proxy = null;
            }

            protected override WebRequest GetWebRequest(Uri address)
            {
                var request = (HttpWebRequest)base.GetWebRequest(address);
                request.Timeout = 30000;
                request.ReadWriteTimeout = 30000;
                return request;
            }

            internal void DownloadPackage(Uri url, string destination, Action<int, string> progress)
            {
                var request = (HttpWebRequest)GetWebRequest(url);
                request.UserAgent = "CorteLive-Setup";
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var input = response.GetResponseStream())
                using (var output = File.Create(destination))
                {
                    long length = response.ContentLength;
                    var buffer = new byte[65536];
                    int last = 10;
                    int read;
                    while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        output.Write(buffer, 0, read);
                        int percent = length > 0 ? 10 + (int)Math.Min(59, output.Length * 59.0 / length) : 10;
                        if (percent <= last) continue;
                        last = percent;
                        progress(percent, "Baixando o pacote…");
                    }
                }
            }
        }
    }

    internal sealed class SetupWindow : Form
    {
        private static readonly Color Canvas = ColorTranslator.FromHtml("#111111");
        private static readonly Color Cream = ColorTranslator.FromHtml("#F7E6C9");
        private static readonly Color Muted = ColorTranslator.FromHtml("#DDCFBB");
        private static readonly Color Surface = ColorTranslator.FromHtml("#22201E");
        private static readonly Color Red = ColorTranslator.FromHtml("#F72C12");
        private static readonly Color Ink = ColorTranslator.FromHtml("#0B0A0A");
        private readonly TextBox folder;
        private readonly Button browse;
        private readonly Button install;
        private readonly Label status;
        private readonly ProgressBar bar;
        private bool busy;
        private bool finished;

        internal SetupWindow()
        {
            Text = "Instalador do CorteLive";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            Font = new Font("Segoe UI", 10);
            AutoScaleMode = AutoScaleMode.Dpi;
            BackColor = Canvas;
            ForeColor = Cream;
            ClientSize = new Size(530, 344);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;

            var mark = new PictureBox { Image = Icon.ToBitmap(), SizeMode = PictureBoxSizeMode.Zoom };
            mark.SetBounds(24, 25, 48, 48);
            Controls.Add(mark);
            var title = AddLabel("CorteLive", 84, 20, 420, 36, Cream);
            title.Font = new Font("Segoe UI", 22, FontStyle.Bold);
            AddLabel("by zerocod", 87, 58, 416, 24, Muted);
            AddLabel("Instale o painel para começar a cortar suas lives.", 24, 100, 482, 26, Muted);
            AddLabel("&Pasta de instalação", 24, 144, 482, 24, Cream);
            folder = new TextBox {
                Text = Setup.DefaultFolder, AccessibleName = "Pasta de instalação", TabIndex = 0,
                BorderStyle = BorderStyle.FixedSingle, BackColor = Surface, ForeColor = Cream
            };
            folder.SetBounds(24, 177, 346, 30);
            Controls.Add(folder);
            browse = AddButton("Procurar…", 380, 168, 126, 44, Surface, Cream);
            browse.TabIndex = 1;
            browse.Click += delegate
            {
                using (var picker = new FolderBrowserDialog { Description = "Escolha a pasta do CorteLive", ShowNewFolderButton = true })
                {
                    if (Directory.Exists(folder.Text)) picker.SelectedPath = folder.Text;
                    if (picker.ShowDialog(this) == DialogResult.OK) folder.Text = picker.SelectedPath;
                }
            };

            bar = new ProgressBar { AccessibleName = "Progresso da instalação", Style = ProgressBarStyle.Continuous };
            bar.SetBounds(24, 225, 482, 8);
            Controls.Add(bar);
            status = AddLabel("O painel abre assim que a instalação terminar.", 24, 243, 482, 38, Muted);
            status.AccessibleName = "Estado da instalação";
            install = AddButton("&Instalar", 364, 284, 142, 44, Red, Ink);
            install.Font = new Font("Segoe UI", 11, FontStyle.Bold);
            install.TabIndex = 2;
            install.Click += InstallClick;
            // Enter só instala de propósito: o foco começa no campo da pasta, onde Espaço e letras
            // viram texto. Com o foco no botão, qualquer Enter, Espaço ou "i" solto disparava a instalação.
            AcceptButton = install;
            ActiveControl = folder;
            FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };
        }

        private Label AddLabel(string text, int x, int y, int width, int height, Color color)
        {
            var label = new Label { Text = text, ForeColor = color };
            label.SetBounds(x, y, width, height);
            Controls.Add(label);
            return label;
        }

        private Button AddButton(string text, int x, int y, int width, int height, Color background, Color foreground)
        {
            var button = new Button { Text = text, BackColor = background, ForeColor = foreground,
                FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand };
            button.SetBounds(x, y, width, height);
            button.FlatAppearance.BorderColor = ControlPaint.Light(background);
            button.FlatAppearance.MouseOverBackColor = ControlPaint.Light(background, 0.14f);
            Controls.Add(button);
            return button;
        }

        private async void InstallClick(object sender, EventArgs e)
        {
            if (finished) { Close(); return; }
            busy = true;
            browse.Enabled = folder.Enabled = install.Enabled = false;
            string target = folder.Text;
            bar.Value = 0;
            status.ForeColor = Muted;
            try
            {
                await Task.Run(delegate
                {
                    Setup.Install(target, (percent, message) => BeginInvoke((Action)delegate
                    {
                        bar.Value = percent;
                        status.Text = percent + "%  " + message;
                    }));
                });
                finished = true;
                install.Text = "&Fechar";
            }
            catch (Exception ex)
            {
                Setup.Log(ex.ToString());
                bar.Value = 0;
                status.ForeColor = ColorTranslator.FromHtml("#FF947E");
                status.Text = "Instalação interrompida. Veja o motivo na mensagem.";
                string message = ex is WebException ? "Não foi possível baixar o pacote. Confira a conexão e tente novamente." : ex.Message;
                MessageBox.Show(this, message + "\n\nLog: " + Setup.LogPath,
                    "Falha na instalação", MessageBoxButtons.OK, MessageBoxIcon.Error);
                browse.Enabled = folder.Enabled = true;
                install.Text = "Tentar &novamente";
            }
            finally
            {
                busy = false;
                install.Enabled = true;
            }
        }
    }
}
