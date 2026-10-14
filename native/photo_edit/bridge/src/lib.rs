//! Thin Python bridge. Upstream algorithms are imported unchanged; no Qt or file writes.
use numpy::{IntoPyArray, PyArray3, PyReadonlyArray3, PyUntypedArrayMethods};
use photocraft_algo::camera_raw::{self, CameraRaw};
use photocraft_compose::{Buffer, adjust::{self, Transfer}};
use photocraft_doc::Adjustment;
use photocraft_geom::Rect;
use pyo3::{exceptions::{PyRuntimeError, PyValueError}, prelude::*};
use serde_json::json;
use std::panic::{AssertUnwindSafe, catch_unwind};

#[path = "../../reference/camera_raw_xmp.rs"]
mod camera_raw_xmp;

const REVISION: &str = include_str!("../../upstream_revision.txt");
const MAX_PIXELS: usize = 64_000_000;
const MAX_PARAMS: usize = 64 * 1024 * 1024;

fn err(e: impl ToString) -> PyErr { PyValueError::new_err(e.to_string()) }

fn decode<T: serde::de::DeserializeOwned>(text: &str) -> PyResult<T> {
    if text.len() > MAX_PARAMS { return Err(err("photo edit parameters exceed 64 MiB")); }
    serde_json::from_str(text).map_err(err)
}

fn defaults() -> Vec<Adjustment> {
    vec![
        Adjustment::BrightnessContrast { brightness: 0., contrast: 0., legacy: false },
        Adjustment::identity_levels(), Adjustment::identity_curves(),
        Adjustment::Exposure { exposure: 0., offset: 0., gamma: 1. },
        Adjustment::Vibrance { vibrance: 0., saturation: 0. }, Adjustment::default_hue_saturation(),
        Adjustment::ColorBalance { shadows: [0.;3], midtones: [0.;3], highlights: [0.;3], preserve_luminosity: true },
        Adjustment::BlackWhite { weights: [40.,60.,40.,60.,20.,80.], tint: None },
        Adjustment::PhotoFilter { color: [1.,0.55,0.], density: 0.25, preserve_luminosity: true },
        Adjustment::ChannelMixer { matrix: [[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.]], monochrome: false },
        Adjustment::ColorLookup { name: String::new(), lut: None, size: 0, tetrahedral: false, dither: false },
        Adjustment::Invert, Adjustment::Posterize { levels: 4 }, Adjustment::Threshold { level: 128. },
        Adjustment::GradientMap { stops: vec![(0.,[0.;3]),(1.,[1.;3])], reverse: false, dither: false },
        Adjustment::SelectiveColor { relative: true, adjustments: [[0.;4];9] },
    ]
}

#[pyfunction]
fn info_json() -> PyResult<String> {
    Ok(json!({"revision":REVISION,"bridgeVersion":1,"maxPixels":MAX_PIXELS,
        "cameraRaw":CameraRaw::default(),"adjustments":defaults(),
        "looks":photocraft_cms::lutfile::BUILTIN}).to_string())
}

fn parse_camera(text: &str) -> PyResult<CameraRaw> {
    let camera: CameraRaw = decode(text)?;
    camera.validate().map_err(err)?;
    // Browser/CLI cannot alter the colour encoding or pixel radii convention.
    Ok(CameraRaw { encoding_gamma: 0., pixel_scale: 1., ..camera })
}

fn parse_adjustments(text: &str) -> PyResult<Vec<Adjustment>> {
    let values: Vec<Adjustment> = decode(text)?;
    if values.len() > 64 { return Err(err("at most 64 adjustment steps")); }
    for value in &values {
        match value {
            Adjustment::Unsupported {..} => return Err(err("unsupported adjustment")),
            Adjustment::ColorLookup { lut: Some(lut), size, .. } => {
                if !(2..=129).contains(size) || photocraft_doc::adjust::lut3d_len(*size) != Some(lut.len()) {
                    return Err(err("LUT dimensions do not match its table"));
                }
            },
            Adjustment::GradientMap { stops, .. } if !(2..=64).contains(&stops.len()) => return Err(err("gradient needs 2–64 stops")),
            Adjustment::Curves { master, per_channel, .. } => {
                for points in std::iter::once(master).chain(per_channel.iter()) {
                    if points.len() > 256 { return Err(err("too many curve points")); }
                }
            },
            _ => (),
        }
    }
    Ok(values)
}

#[pyfunction]
fn validate_json(camera_json: &str, adjustments_json: &str) -> PyResult<String> {
    let camera = parse_camera(camera_json)?;
    let adjustments = parse_adjustments(adjustments_json)?;
    Ok(json!({"cameraRaw":camera,"adjustments":adjustments}).to_string())
}

fn process_checked(mut px: Vec<[f32;4]>, width: usize, height: usize, camera: &CameraRaw, steps: &[Adjustment],
                   mut checkpoint: impl FnMut() -> PyResult<()>) -> PyResult<Vec<[f32;4]>> {
    checkpoint()?;
    camera_raw::develop(&mut px, width, height, camera, false);
    checkpoint()?;
    let mut buffer = Buffer { rect: Rect::new(0,0,width as i32,height as i32), px };
    for step in steps {
        checkpoint()?;
        adjust::apply_with(step, &mut buffer, Transfer::Srgb);
    }
    checkpoint()?;
    for pixel in &mut buffer.px { for channel in pixel { *channel = channel.clamp(0.,1.); } }
    Ok(buffer.px)
}

#[cfg(test)]
fn process(px: Vec<[f32;4]>, width: usize, height: usize, camera: &CameraRaw, steps: &[Adjustment]) -> Vec<[f32;4]> {
    process_checked(px,width,height,camera,steps,|| Ok(())).unwrap()
}

#[pyfunction]
#[pyo3(signature=(pixels, camera_json, adjustments_json, pixel_scale=1.0, checkpoint=None))]
fn render<'py>(py: Python<'py>, pixels: PyReadonlyArray3<'py,f32>, camera_json: &str, adjustments_json: &str,
               pixel_scale: f32, checkpoint: Option<Py<PyAny>>) -> PyResult<Bound<'py,PyArray3<f32>>> {
    let shape = pixels.shape();
    let (height,width) = (shape[0],shape[1]);
    if shape[2] != 4 || width == 0 || height == 0 || width.checked_mul(height).is_none_or(|n| n > MAX_PIXELS) {
        return Err(err("expected nonempty H×W×4 float32 pixels, at most 64 megapixels"));
    }
    if !pixel_scale.is_finite() || pixel_scale <= 0. || pixel_scale > 1. {
        return Err(err("pixel_scale must be in (0,1]"));
    }
    let slice = pixels.as_slice().map_err(err)?;
    if slice.iter().any(|v| !v.is_finite() || !(0.0..=1.0).contains(v)) {
        return Err(err("SDR pixels must be finite and in [0,1]"));
    }
    // Own the pixels before detaching; Python may change the originating ndarray.
    let input: Vec<[f32;4]> = slice.chunks_exact(4).map(|q| [q[0],q[1],q[2],q[3]]).collect();
    let mut camera = parse_camera(camera_json)?;
    camera.pixel_scale = pixel_scale;
    let steps = parse_adjustments(adjustments_json)?;
    let result = py.detach(move || catch_unwind(AssertUnwindSafe(|| process_checked(input,width,height,&camera,&steps,|| {
        match &checkpoint {
            Some(callback) => Python::attach(|python| callback.call0(python).map(|_| ())),
            None => Ok(()),
        }
    }))))
        .map_err(|_| PyRuntimeError::new_err("PhotoCraft pixel processing failed; source pixels were not modified"))??;
    let flat: Vec<f32> = result.into_iter().flatten().collect();
    let array = numpy::ndarray::Array3::from_shape_vec((height,width,4),flat).map_err(err)?;
    Ok(array.into_pyarray(py))
}

#[pyfunction]
fn import_settings_json(camera_json: &str, xmp: &[u8]) -> PyResult<String> {
    let mut camera = parse_camera(camera_json)?;
    let report = camera_raw_xmp::load(&mut camera,xmp).map_err(err)?;
    Ok(json!({"cameraRaw":camera,"applied":report.applied,"ignored":report.ignored}).to_string())
}

#[pyfunction]
fn export_settings(camera_json: &str) -> PyResult<String> {
    Ok(camera_raw_xmp::save(&parse_camera(camera_json)?))
}

#[pyfunction]
#[pyo3(signature=(file_name, data, builtin=None))]
fn load_lut_json(file_name: &str, data: &[u8], builtin: Option<&str>) -> PyResult<String> {
    if data.len() > MAX_PARAMS { return Err(err("LUT file exceeds 64 MiB")); }
    let lut = match builtin {
        Some(id) => photocraft_cms::lutfile::builtin(id).ok_or_else(|| err("unknown built-in LUT"))?,
        None => photocraft_cms::lutfile::parse(file_name,data).map_err(err)?,
    };
    if !lut.domain_is_default() { return Err(err("only LUT input domain [0,1] is supported")); }
    Ok(json!({"name":lut.title,"size":lut.size,"lut":lut.data,"cube":photocraft_cms::lutfile::write_cube(&lut)}).to_string())
}

#[pymodule]
fn _native(module: &Bound<'_,PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(info_json,module)?)?;
    module.add_function(wrap_pyfunction!(validate_json,module)?)?;
    module.add_function(wrap_pyfunction!(render,module)?)?;
    module.add_function(wrap_pyfunction!(import_settings_json,module)?)?;
    module.add_function(wrap_pyfunction!(export_settings,module)?)?;
    module.add_function(wrap_pyfunction!(load_lut_json,module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bridge_matches_upstream_camera_and_adjustments() {
        let input: Vec<[f32;4]> = (0..256).map(|i| [i as f32/256.,0.3,0.7,0.75]).collect();
        let camera = CameraRaw { exposure: 0.6, clarity: 10., ..Default::default() };
        for step in defaults() {
            let mut expected = input.clone();
            camera_raw::develop(&mut expected,16,16,&camera,false);
            let mut b = Buffer { rect: Rect::new(0,0,16,16), px: expected };
            adjust::apply_with(&step,&mut b,Transfer::Srgb);
            for p in &mut b.px { for c in p { *c = c.clamp(0.,1.); } }
            assert_eq!(process(input.clone(),16,16,&camera,&[step]),b.px);
        }
    }
    #[test]
    fn neutral_camera_preserves_float_precision() {
        let px = vec![[0.123456,0.345678,0.987654,0.1234];16];
        assert_eq!(process(px.clone(),4,4,&CameraRaw::default(),&[]),px);
    }
}
