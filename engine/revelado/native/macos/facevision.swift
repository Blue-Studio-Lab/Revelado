// SPDX-License-Identifier: GPL-3.0-or-later
// facevision: detecta caras (con puntos de referencia) y personas con el framework Vision de macOS.
// Uso: facevision IMAGEN.jpg  -> JSON en stdout, coordenadas normalizadas con origen ARRIBA-izquierda.
import Foundation
import Vision
import CoreImage

let args = CommandLine.arguments
guard args.count > 1 else { print("{\"error\":\"uso\"}"); exit(1) }
let url = URL(fileURLWithPath: args[1])
let handler = VNImageRequestHandler(url: url, options: [:])
let faceReq = VNDetectFaceLandmarksRequest()
let humanReq = VNDetectHumanRectanglesRequest()
humanReq.upperBodyOnly = false
do { try handler.perform([faceReq, humanReq]) } catch { print("{\"error\":\"\(error)\"}"); exit(2) }

func pts(_ r: VNFaceLandmarkRegion2D?, _ bb: CGRect) -> [[Double]] {
    guard let r = r else { return [] }
    return r.normalizedPoints.map { p in
        let x = Double(bb.origin.x + p.x * bb.size.width)
        let y = Double(bb.origin.y + p.y * bb.size.height)
        return [x, 1.0 - y]
    }
}
var faces: [[String: Any]] = []
for f in faceReq.results ?? [] {
    let bb = f.boundingBox
    var d: [String: Any] = ["box": [Double(bb.origin.x), Double(1 - bb.origin.y - bb.size.height), Double(bb.size.width), Double(bb.size.height)],
                            "conf": Double(f.confidence)]
    if let yaw = f.yaw { d["yaw"] = yaw.doubleValue }
    if let lm = f.landmarks {
        d["leftEye"] = pts(lm.leftEye, bb); d["rightEye"] = pts(lm.rightEye, bb)
        d["leftEyebrow"] = pts(lm.leftEyebrow, bb); d["rightEyebrow"] = pts(lm.rightEyebrow, bb)
        d["outerLips"] = pts(lm.outerLips, bb); d["nose"] = pts(lm.nose, bb)
        d["faceContour"] = pts(lm.faceContour, bb)
    }
    faces.append(d)
}
var humans: [[Double]] = []
for h in humanReq.results ?? [] {
    let bb = h.boundingBox
    humans.append([Double(bb.origin.x), Double(1 - bb.origin.y - bb.size.height), Double(bb.size.width), Double(bb.size.height), Double(h.confidence)])
}
let out: [String: Any] = ["faces": faces, "humans": humans]
let data = try! JSONSerialization.data(withJSONObject: out)
print(String(data: data, encoding: .utf8)!)
