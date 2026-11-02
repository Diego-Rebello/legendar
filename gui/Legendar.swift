// Janela para arrastar um MKV. Toda a lógica fica em scripts/legendar.py.
// Compilar com gui/build.sh.
import SwiftUI

struct Step: Identifiable {
    enum State { case pending, active, done, failed }

    let id: Int
    let name: String
    var state = State.pending
    var percent = 0
    var detail = ""
}

final class Job: ObservableObject {
    static let shared = Job()
    private static let names = ["Extrair a legenda em inglês", "Remover efeitos, sons, música e nomes", "Traduzir com o Sonnet", "Gravar o SRT"]
    private static let extract = 0, clean = 1, translate = 2, write = 3

    enum Phase: Equatable {
        case idle
        case checking
        case ready(calls: Int)
        case running
        case finished(URL)
        case failed(String)
    }

    @Published private(set) var phase = Phase.idle
    @Published private(set) var video: URL?
    @Published private(set) var log = ""
    @Published private(set) var steps = Job.freshSteps()
    @Published private(set) var callStart: Date?
    @Published private(set) var refused = 0
    @Published private(set) var existingOutput: String?
    @Published private(set) var notice: String?
    @Published var dropTargeted = false
    private var process: Process?
    private var buffer = Data()
    private var calls = 0
    private var retryCalls = 0
    private var generated: URL?
    private var cancelled = false

    private static func freshSteps() -> [Step] {
        names.enumerated().map { Step(id: $0.offset, name: $0.element) }
    }

    var busy: Bool { phase == .checking || phase == .running }

    // Pesos estimados: quase todo o tempo passa nas chamadas ao modelo.
    var overallPercent: Int {
        let local = Double(steps[Job.extract].percent + steps[Job.clean].percent) / 2
        return Int(0.10 * local + 0.85 * Double(steps[Job.translate].percent) + 0.05 * Double(steps[Job.write].percent))
    }

    // Uma rodada por vez: o plano Pro tem limite baixo.
    func receive(_ urls: [URL]) -> Bool {
        guard !busy else { return false }
        // Não mexe em `phase`: um arquivo errado não pode tirar o Traduzir do vídeo atual.
        guard urls.count == 1, urls[0].pathExtension.lowercased() == "mkv" else {
            notice = "Arraste um único arquivo .mkv."
            return false
        }
        video = urls[0]
        check()
        return true
    }

    func check() {
        calls = 0
        retryCalls = 0
        refused = 0
        existingOutput = nil
        notice = nil
        log = ""
        steps = Job.freshSteps()
        steps[Job.extract].state = .active
        phase = .checking
        launch(["--dry-run"]) { status in
            if status == 0 {
                self.phase = .ready(calls: self.refused > 0 ? self.retryCalls : self.calls)
            } else {
                self.fail("Não deu para ler o vídeo: \(self.log.split(separator: "\n").last ?? "sem mensagem")")
            }
        }
    }

    func start() {
        generated = nil
        notice = nil
        phase = .running
        // Sem as recusadas não há SRT: ao confirmar, elas vão junto com o que faltava.
        let retry = refused > 0
        begin(calls == 0 && !retry ? Job.write : Job.translate)
        launch(retry ? ["--retry-refused"] : []) { status in
            if let generated = self.generated {
                self.steps[Job.write].state = .done
                self.steps[Job.write].percent = 100
                self.phase = .finished(generated)
            } else if status == 0 {
                self.fail("SRT não gerado: ainda há legendas sem tradução. Veja o registro.")
            } else {
                self.fail("Parou. O que já foi traduzido está no cache, e o motivo está no registro abaixo.")
            }
        }
    }

    func cancel() {
        guard let process, process.isRunning else { return }
        cancelled = true
        // Ao receber SIGTERM, legendar.py encerra o `claude` ou o mkvextract em andamento.
        process.terminate()
    }

    private func begin(_ step: Int) {
        for earlier in 0..<step {
            steps[earlier].state = .done
            steps[earlier].percent = 100
        }
        steps[step].state = .active
    }

    private func fail(_ message: String) {
        callStart = nil
        for i in steps.indices where steps[i].state == .active {
            steps[i].state = .failed
        }
        phase = .failed(message)
    }

    private func launch(_ extra: [String], then finish: @escaping (Int32) -> Void) {
        let root = Bundle.main.object(forInfoDictionaryKey: "LegendarRoot") as! String
        var env = ProcessInfo.processInfo.environment
        // Aberto pelo Finder, o app não herda o PATH do terminal. Sem USER, o claude não acha o login.
        let home = NSHomeDirectory()
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
        env["USER"] = NSUserName()

        let pipe = Pipe()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        // -u: sem ele, a saída só chegaria aqui no fim da rodada. -I vem do CLAUDE.md, -X utf8 é para o Finder, sem LANG.
        p.arguments = ["python3", "-I", "-u", "-X", "utf8", "\(root)/scripts/legendar.py", "run", video!.path,
                       "--model", "sonnet", "--effort", "medium", "--max-blocks", "9999"] + extra
        p.currentDirectoryURL = URL(fileURLWithPath: root)
        p.environment = env
        p.standardOutput = pipe
        p.standardError = pipe
        p.standardInput = FileHandle.nullDevice
        do {
            try p.run()
        } catch {
            fail("Não consegui iniciar o python3: \(error.localizedDescription)")
            return
        }
        process = p
        cancelled = false
        DispatchQueue.global().async {
            let handle = pipe.fileHandleForReading
            while true {
                let data = handle.availableData
                if data.isEmpty { break }
                DispatchQueue.main.async { self.consume(data) }
            }
            p.waitUntilExit()
            DispatchQueue.main.async {
                self.consume(Data("\n".utf8))
                self.process = nil
                if self.cancelled {
                    self.fail("Cancelado. O que já foi traduzido está no cache.")
                } else {
                    finish(p.terminationStatus)
                }
            }
        }
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let end = buffer.firstIndex(of: 10) {
            let line = String(decoding: buffer[buffer.startIndex..<end], as: UTF8.self)
            buffer = Data(buffer[buffer.index(after: end)...])
            if !line.isEmpty { handle(line) }
        }
    }

    // Estes textos vêm de legendar.py e estão travados em tests/test_legendar.py.
    private func handle(_ line: String) {
        if line.hasPrefix("Extraindo: ") {
            if phase == .checking {
                steps[Job.extract].percent = Int(line.dropFirst("Extraindo: ".count).dropLast()) ?? 0
            }
            return
        }
        log += line + "\n"
        if line.hasPrefix("Trilha ") {
            // `run` reaproveita o que a verificação extraiu: a etapa continua concluída.
            if phase == .checking { begin(Job.clean) }
        } else if let r = line.range(of: "Blocos nesta rodada: "), let n = Int(line[r.upperBound...]) {
            calls = n
            if phase == .checking { begin(Job.translate); steps[Job.translate].state = .pending }
        } else if line.hasPrefix("Progresso: ") {
            progress(line)
        } else if line.hasPrefix("Recusadas: ") {
            // "Recusadas: 100 legendas, 1 chamada(s) para tentar de novo"
            let numbers = line.split(separator: " ").compactMap { Int($0) }
            if numbers.count == 2 { (refused, retryCalls) = (numbers[0], numbers[1]) }
        } else if line.hasPrefix("Saída já existe: ") {
            existingOutput = URL(fileURLWithPath: String(line.dropFirst("Saída já existe: ".count))).lastPathComponent
        } else if line.hasPrefix("Traduzindo: ") {
            steps[Job.translate].detail = String(line.dropFirst("Traduzindo: ".count))
            callStart = Date()
        } else if line.hasPrefix("Bloco ") {
            callStart = nil
        } else if line.hasPrefix("Gerado ") {
            generated = URL(fileURLWithPath: String(line.dropFirst("Gerado ".count)))
        }
    }

    // "Progresso: 350 de 751 legendas": conta o cache, então retomar não recomeça do zero.
    private func progress(_ line: String) {
        let numbers = line.split(separator: " ").compactMap { Int($0) }
        guard numbers.count == 2, numbers[1] > 0 else { return }
        let (done, total) = (numbers[0], numbers[1])
        steps[Job.clean].detail = "\(total) legendas com fala"
        steps[Job.translate].percent = done * 100 / total
        if phase == .checking {
            steps[Job.translate].detail = done == 0 ? "" : "\(done) de \(total) já em cache"
            if done == total { steps[Job.translate].state = .done }
        } else if done == total {
            steps[Job.translate].detail = "\(total) de \(total) legendas"
            begin(Job.write)
        }
    }
}

struct StepRow: View {
    let step: Step
    let callStart: Date?

    var body: some View {
        HStack(spacing: 8) {
            icon.frame(width: 18)
            Text(step.name).foregroundStyle(step.state == .pending ? .secondary : .primary)
            Spacer()
            if step.state == .active, let callStart {
                // Durante a chamada o percentual fica parado: os segundos mostram que não travou.
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text("\(step.detail), \(Int(context.date.timeIntervalSince(callStart))) s")
                }
                .font(.caption).foregroundStyle(.secondary)
            } else {
                Text(step.detail).font(.caption).foregroundStyle(.secondary)
            }
            Text("\(step.percent)%").monospacedDigit().frame(width: 44, alignment: .trailing)
        }
    }

    @ViewBuilder private var icon: some View {
        switch step.state {
        case .pending: Image(systemName: "circle").foregroundStyle(.secondary)
        case .active: ProgressView().controlSize(.small)
        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        }
    }
}

struct ContentView: View {
    @ObservedObject var job = Job.shared

    var body: some View {
        VStack(spacing: 14) {
            dropZone
            stepList
            if let notice = job.notice {
                Text(notice).foregroundStyle(.orange)
            }
            status
            actions
            logView
        }
        .padding(18)
        .frame(minWidth: 620, minHeight: 620)
        .onOpenURL { _ = job.receive([$0]) }
    }

    private var dropZone: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(job.dropTargeted ? Color.accentColor : .secondary, style: StrokeStyle(lineWidth: 2, dash: [7]))
                .background(job.dropTargeted ? Color.accentColor.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 12))
            VStack(spacing: 6) {
                Image(systemName: "film").font(.system(size: 26)).foregroundStyle(.secondary)
                Text(job.video?.lastPathComponent ?? "Arraste o MKV aqui")
                    .font(.headline)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .padding(.horizontal)
            }
        }
        .frame(height: 110)
        .contentShape(Rectangle())
        .dropDestination(for: URL.self) { urls, _ in job.receive(urls) } isTargeted: { job.dropTargeted = $0 }
    }

    private var stepList: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(job.steps) { StepRow(step: $0, callStart: job.callStart) }
            Divider()
            HStack {
                ProgressView(value: Double(job.overallPercent), total: 100)
                Text("\(job.overallPercent)%").font(.headline).monospacedDigit().frame(width: 52, alignment: .trailing)
            }
        }
    }

    @ViewBuilder private var status: some View {
        switch job.phase {
        case .idle:
            Text("Sonnet em esforço médio, pela assinatura. Nada vai ao modelo antes da sua confirmação.")
                .foregroundStyle(.secondary)
        case .checking, .running:
            EmptyView()
        case .ready(let calls):
            VStack(spacing: 4) {
                if job.refused > 0 {
                    Text("O modelo recusou \(job.refused) legendas, e sem elas não há SRT. Tentar de novo: \(calls) chamada(s) ao Sonnet, que gastam limite do plano.")
                        .multilineTextAlignment(.center)
                } else {
                    Text(calls == 0 ? "Tudo em cache: nenhuma chamada ao modelo." : "Falta confirmar: \(calls) chamada(s) ao Sonnet, que gastam limite do plano.")
                }
                if let name = job.existingOutput {
                    Text("Já existe \(name), que será substituído.").foregroundStyle(.orange)
                }
            }
        case .finished(let url):
            Label("Pronto: \(url.lastPathComponent)", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).lineLimit(3)
        }
    }

    @ViewBuilder private var actions: some View {
        HStack {
            switch job.phase {
            case .idle:
                EmptyView()
            case .checking, .running:
                Button("Cancelar") { job.cancel() }
            case .ready(let calls):
                Button(job.refused > 0 ? "Tentar as recusadas" : calls == 0 ? "Gerar SRT" : "Traduzir") { job.start() }
                    .keyboardShortcut(.defaultAction)
            case .finished(let url):
                Button("Mostrar no Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
            case .failed:
                if job.video != nil {
                    Button("Tentar de novo") { job.check() }
                }
            }
        }
    }

    private var logView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Text(job.log)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                Color.clear.frame(height: 1).id("fim")
            }
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            .onChange(of: job.log) { proxy.scrollTo("fim") }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { Job.shared.cancel() }
}

@main
struct LegendarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        Window("Legendar", id: "main") { ContentView() }
    }
}
