(() => {
  const form = document.getElementById("note-form");
  const textoInput = document.getElementById("note-texto");
  const textoError = document.getElementById("texto-error");
  const fotoInput = document.getElementById("note-foto");
  const fotoError = document.getElementById("foto-error");
  const submitBtn = document.getElementById("submit-btn");
  const previewWrapper = document.getElementById("preview-wrapper");
  const previewImg = document.getElementById("photo-preview");
  const previewMeta = document.getElementById("preview-meta");

  const ALLOWED_TYPES = new Set([
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
  ]);
  const MAX_MB = Number(form?.dataset.maxMb || 5);
  const MAX_BYTES = MAX_MB * 1024 * 1024;
  let currentObjectUrl = null;
  let textoEdited = false;

  const setFieldError = (input, errorEl, message) => {
    input.setCustomValidity(message);
    errorEl.textContent = message;
    if (message) {
      input.setAttribute("aria-invalid", "true");
    } else {
      input.removeAttribute("aria-invalid");
    }
  };

  const validateTexto = () => {
    const value = textoInput.value.trim();
    if (!value) {
      setFieldError(
        textoInput,
        textoError,
        "Escreva uma mensagem para fixar sua nota no mural."
      );
      return false;
    }
    setFieldError(textoInput, textoError, "");
    return true;
  };

  const clearPreview = () => {
    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl);
      currentObjectUrl = null;
    }
    previewImg.removeAttribute("src");
    previewMeta.textContent = "";
    previewWrapper.hidden = true;
  };

  const validateFoto = () => {
    const file = fotoInput.files?.[0];
    if (!file) {
      clearPreview();
      setFieldError(fotoInput, fotoError, "Selecione uma imagem para enviar ao S3.");
      return false;
    }
    if (!ALLOWED_TYPES.has(file.type)) {
      clearPreview();
      setFieldError(
        fotoInput,
        fotoError,
        "Formato inválido. Use JPG, PNG, WEBP ou GIF."
      );
      return false;
    }
    if (file.size > MAX_BYTES) {
      clearPreview();
      setFieldError(
        fotoInput,
        fotoError,
        `A imagem excede o limite de ${MAX_MB} MB.`
      );
      return false;
    }

    setFieldError(fotoInput, fotoError, "");
    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl);
    }
    currentObjectUrl = URL.createObjectURL(file);
    previewImg.src = currentObjectUrl;
    const sizeKb = Math.max(1, Math.round(file.size / 1024));
    previewMeta.textContent = `${file.name} (${sizeKb} KB)`;
    previewWrapper.hidden = false;
    return true;
  };

  if (form && textoInput && fotoInput) {
    textoInput.addEventListener("input", () => {
      textoEdited = true;
      if (textoInput.value.trim()) {
        setFieldError(textoInput, textoError, "");
      }
    });

    textoInput.addEventListener("blur", () => {
      if (textoEdited) {
        validateTexto();
      }
    });

    fotoInput.addEventListener("change", () => {
      validateFoto();
    });

    form.addEventListener("submit", (event) => {
      const textoOk = validateTexto();
      const fotoOk = validateFoto();
      if (!textoOk || !fotoOk) {
        event.preventDefault();
        const firstInvalid = !textoOk ? textoInput : fotoInput;
        firstInvalid.focus();
        return;
      }
      submitBtn.disabled = true;
      submitBtn.textContent = "Enviando para o mural...";
    });
  }

  // Modal nativo de confirmação antes de remover nota e objeto no S3
  const deleteDialog = document.getElementById("delete-dialog");
  const deleteNoteName = document.getElementById("delete-note-name");
  const deleteConfirmForm = document.getElementById("delete-confirm-form");

  if (deleteDialog && deleteNoteName && deleteConfirmForm) {
    for (const btn of document.querySelectorAll(".js-delete-trigger")) {
      btn.addEventListener("click", () => {
        deleteConfirmForm.action = btn.dataset.deleteAction || "";
        deleteNoteName.textContent = btn.dataset.noteLabel || "selecionada";
        deleteDialog.showModal();
      });
    }

    deleteDialog.addEventListener("close", () => {
      deleteDialog.returnValue = "";
    });

    // Fallback de light-dismiss caso o navegador ainda não suporte dialog[closedby="any"]
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      const outside = (e, dialog) => {
        const r = dialog.getBoundingClientRect();
        return (
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom
        );
      };
      let pressedOutside = false;
      deleteDialog.addEventListener("pointerdown", (e) => {
        pressedOutside = outside(e, deleteDialog);
      });
      deleteDialog.addEventListener("pointerup", (e) => {
        if (pressedOutside && outside(e, deleteDialog)) {
          deleteDialog.close();
        }
      });
    }
  }
})();
